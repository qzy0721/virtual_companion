import { computed, ref } from 'vue'
import pcmWorkletUrl from '../worklets/asr-pcm-worklet.js?url'
import { asrWsUrl } from '../api'

export function useAsrStream({ onComplete, onError }) {
  const mode = ref('idle')
  const status = ref('')
  const draft = ref('')
  const isActive = computed(() => mode.value !== 'idle')
  let socket = null
  let socketPromise = null
  let ready = false
  let stopRequested = false
  let audioContext = null
  let mediaStream = null
  let source = null
  let worklet = null
  let silence = null
  let flushResolver = null
  let pendingFrames = []
  let pendingBytes = 0
  let finalSegments = []
  let partialText = ''

  function transcript() {
    return [...finalSegments, partialText].filter(Boolean).join('')
  }

  function updateDraft() {
    draft.value = transcript()
  }

  function sendFrame(frame) {
    if (socket?.readyState !== WebSocket.OPEN || !ready || socket.bufferedAmount > 128 * 1024) return false
    socket.send(frame)
    return true
  }

  function queueFrame(frame) {
    if (mode.value === 'idle') return
    if (sendFrame(frame)) return
    pendingFrames.push(frame)
    pendingBytes += frame.byteLength
    while (pendingBytes > 32 * 1024 && pendingFrames.length > 1) pendingBytes -= pendingFrames.shift().byteLength
  }

  function flushFrames() {
    while (pendingFrames.length && sendFrame(pendingFrames[0])) pendingBytes -= pendingFrames.shift().byteLength
  }

  async function stopCapture(flush = false) {
    if (flush && worklet) {
      await Promise.race([
        new Promise((resolve) => {
          flushResolver = resolve
          worklet.port.postMessage({ type: 'flush' })
        }),
        new Promise((resolve) => window.setTimeout(resolve, 300)),
      ])
      flushResolver = null
    }
    try { source?.disconnect() } catch {}
    try { worklet?.disconnect(); worklet?.port.close() } catch {}
    try { silence?.disconnect() } catch {}
    mediaStream?.getTracks().forEach((track) => track.stop())
    try { await audioContext?.close() } catch {}
    source = null
    worklet = null
    silence = null
    mediaStream = null
    audioContext = null
  }

  async function finishTurn() {
    await stopCapture()
    mode.value = 'idle'
    status.value = ''
    ready = false
    stopRequested = false
    pendingFrames = []
    pendingBytes = 0
    partialText = ''
    finalSegments = []
  }

  function attachSocketEvents(currentSocket) {
    currentSocket.onmessage = async (event) => {
      let message
      try { message = JSON.parse(event.data) } catch { return }
      if (message.type === 'ready') {
        ready = true
        flushFrames()
        if (stopRequested) currentSocket.send(JSON.stringify({ type: 'stop' }))
        else {
          mode.value = 'recording'
          status.value = '正在聆听...'
        }
      } else if (message.type === 'partial') {
        partialText = message.text || ''
        status.value = '正在识别...'
        updateDraft()
      } else if (message.type === 'final') {
        const text = (message.text || '').trim()
        if (text) finalSegments.push(text)
        partialText = ''
        status.value = '正在识别...'
        updateDraft()
      } else if (message.type === 'done') {
        if (mode.value === 'idle') return
        const serverText = (message.text || '').trim()
        const localText = transcript().trim()
        const text = serverText.length >= localText.length ? serverText : localText
        await finishTurn()
        onComplete?.(text)
      } else if (message.type === 'error') {
        await finishTurn()
        onError?.(message.message || '语音识别失败')
      }
    }
    currentSocket.onclose = async () => {
      if (socket !== currentSocket) return
      socket = null
      socketPromise = null
      if (mode.value !== 'idle') {
        await finishTurn()
        onError?.('语音连接已断开')
      }
    }
  }

  function ensureSocket() {
    if (socket?.readyState === WebSocket.OPEN) return Promise.resolve(socket)
    if (socketPromise) return socketPromise
    socket = new WebSocket(asrWsUrl())
    attachSocketEvents(socket)
    socketPromise = new Promise((resolve, reject) => {
      const currentSocket = socket
      currentSocket.onopen = () => {
        socketPromise = null
        resolve(currentSocket)
      }
      currentSocket.onerror = () => {
        socketPromise = null
        reject(new Error('无法连接实时语音服务'))
        currentSocket.close()
      }
    })
    return socketPromise
  }

  async function startCapture() {
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) throw new Error('当前浏览器不支持实时语音输入')
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    })
    audioContext = new (window.AudioContext || window.webkitAudioContext)()
    await audioContext.audioWorklet.addModule(pcmWorkletUrl)
    await audioContext.resume()
    if (stopRequested || mode.value === 'idle') {
      await stopCapture()
      return
    }
    source = audioContext.createMediaStreamSource(mediaStream)
    worklet = new AudioWorkletNode(audioContext, 'pcm-capture-processor', {
      processorOptions: { targetSampleRate: 16000, frameMs: 100 },
    })
    silence = audioContext.createGain()
    silence.gain.value = 0
    worklet.port.onmessage = (event) => {
      if (event.data?.type === 'pcm') queueFrame(event.data.data)
      if (event.data?.type === 'flushed' && flushResolver) {
        flushResolver()
        flushResolver = null
      }
    }
    source.connect(worklet).connect(silence).connect(audioContext.destination)
  }

  async function start() {
    if (mode.value !== 'idle') return
    mode.value = 'connecting'
    status.value = '正在连接实时识别...'
    ready = false
    stopRequested = false
    draft.value = ''
    finalSegments = []
    partialText = ''
    pendingFrames = []
    pendingBytes = 0
    try {
      await startCapture()
      const currentSocket = await ensureSocket()
      if (mode.value === 'idle') return
      currentSocket.send(JSON.stringify({ type: 'start', language_hints: ['zh', 'ja', 'en'] }))
    } catch (error) {
      await finishTurn()
      onError?.(error.message || '无法启动语音输入')
    }
  }

  async function stop() {
    if (mode.value === 'idle') return
    stopRequested = true
    mode.value = 'stopping'
    status.value = '正在识别...'
    await stopCapture(true)
    if (ready && socket?.readyState === WebSocket.OPEN) {
      flushFrames()
      socket.send(JSON.stringify({ type: 'stop' }))
    }
  }

  async function toggle() {
    if (mode.value === 'idle') await start()
    else await stop()
  }

  async function dispose() {
    await finishTurn()
    socket?.close()
    socket = null
  }

  // start/stop 一并暴露：对话模式（无感交流）需要显式控制每轮录音
  return { mode, status, draft, isActive, start, stop, toggle, dispose }
}
