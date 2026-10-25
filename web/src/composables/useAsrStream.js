import { computed, ref } from 'vue'
import pcmWorkletUrl from '../worklets/asr-pcm-worklet.js?url'
import { asrWsUrl } from '../api'
import { VoiceActivityDetector } from './useVad'

// 40ms 一帧：VAD 的确认/挂起窗口可以落在 120-200ms，比原来的 100ms 更跟手。
const FRAME_MS = 40
// 说话开始前保留约 600ms 音频。VAD 确认说话时，把这段补发给云 ASR，避免丢首字。
const PRE_ROLL_FRAMES = 15
const MAX_PENDING_BYTES = 48 * 1024
const START_TIMEOUT_MS = 10000
const DONE_TIMEOUT_MS = 7000
const START_ATTEMPTS = 3

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function pcmRms(buffer) {
  const samples = new Int16Array(buffer)
  if (!samples.length) return 0
  let sum = 0
  let sumSquares = 0
  for (let index = 0; index < samples.length; index += 1) {
    const value = samples[index] / 32768
    sum += value
    sumSquares += value * value
  }
  const mean = sum / samples.length
  // 去掉直流偏置，避免某些声卡的低频偏移把 RMS 抬高。
  return Math.sqrt(Math.max(0, sumSquares / samples.length - mean * mean))
}

async function acquireMicrophone() {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error('当前浏览器不支持麦克风采集')
  const base = {
    channelCount: 1,
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  }
  // 浏览器 AEC 默认用 WebRTC 的 browser 实现；system AEC 在部分 Windows
  // 声卡上反而会放行更多扬声器残留，因此只作为最后回退。
  const attempts = [
    base,
    { ...base, echoCancellationType: 'browser' },
    { ...base, echoCancellationType: 'system' },
  ]
  let lastError = null
  for (const audio of attempts) {
    try {
      return await navigator.mediaDevices.getUserMedia({ audio })
    } catch (error) {
      lastError = error
    }
  }
  throw lastError || new Error('无法打开麦克风')
}

/**
 * 浏览器 AEC + 本地 VAD + 云端实时 ASR。
 *
 * - 对话模式（keepAlive）下麦克风常驻，VAD 检测到说话才启动一轮云识别；
 *   说话结束（本地静音判据）立即 finish-task，拿回本轮文本。
 * - 角色说话时 VAD 仍工作，因此可以在 TTS 播放中打断。
 * - 普通语音按钮（非 keepAlive）沿用“按下录音、再按结束”的交互。
 */
export function useAsrStream({
  onComplete,
  onError,
  onSpeechStart,
  onSpeechEnd,
  onTranscript,
  vadOptions,
} = {}) {
  const mode = ref('idle')
  const status = ref('')
  const draft = ref('')
  const speechActive = ref(false)
  const speechLevel = ref(0)
  const isActive = computed(() => mode.value !== 'idle')

  let socket = null
  let socketPromise = null
  let ready = false
  let taskActive = false
  let keepAlive = false
  let stopRequested = false
  let finishSent = false
  let audioContext = null
  let mediaStream = null
  let source = null
  let worklet = null
  let silence = null
  let flushResolver = null
  let pendingFrames = []
  let pendingBytes = 0
  let preRoll = []
  let finalSegments = []
  let partialText = ''
  let vad = null
  let bargeMode = false
  let currentStart = null
  let currentTurn = null
  let finalizing = false
  let levelTick = 0
  let turnSeq = 0

  function createTurn() {
    let resolve
    const promise = new Promise((onResolve) => {
      resolve = onResolve
    })
    return {
      id: ++turnSeq,
      promise,
      resolve,
      settled: false,
      stopping: false,
      emit: true,
    }
  }

  function settleTurn(text) {
    if (!currentTurn || currentTurn.settled) return
    currentTurn.settled = true
    currentTurn.resolve(text)
  }

  function transcript() {
    return [...finalSegments, partialText].filter(Boolean).join('')
  }

  function resetTranscript() {
    finalSegments = []
    partialText = ''
    draft.value = ''
  }

  function updateDraft() {
    draft.value = transcript()
  }

  function sendFrame(frame) {
    if (!taskActive || !ready) return false
    if (socket?.readyState !== WebSocket.OPEN || !socket || socket.bufferedAmount > 128 * 1024) {
      return false
    }
    socket.send(frame)
    return true
  }

  function queueFrame(frame) {
    if (mode.value === 'stopping' || mode.value === 'monitoring' || mode.value === 'idle') return
    if (sendFrame(frame)) return
    pendingFrames.push(frame)
    pendingBytes += frame.byteLength
    while (pendingBytes > MAX_PENDING_BYTES && pendingFrames.length > 1) {
      pendingBytes -= pendingFrames.shift().byteLength
    }
  }

  function flushFrames() {
    while (pendingFrames.length && sendFrame(pendingFrames[0])) {
      pendingBytes -= pendingFrames.shift().byteLength
    }
  }

  function failStart(error, target = currentStart) {
    const start = target
    if (!start || currentStart !== start) return
    currentStart = null
    window.clearTimeout(start.timer)
    start.reject(error)
  }

  function handleServerError(message) {
    if (currentStart) {
      failStart(new Error(message))
      return
    }
    if (mode.value === 'idle' || mode.value === 'monitoring') return
    settleTurn('')
    const hadTurn = Boolean(currentTurn)
    void (async () => {
      if (hadTurn && currentTurn) await finalizeTurn('', { emit: false })
      onError?.(message)
    })()
  }

  function attachSocketEvents(currentSocket) {
    currentSocket.onmessage = (event) => {
      let message
      try {
        message = JSON.parse(event.data)
      } catch {
        return
      }
      if (message.type === 'ready') {
        ready = true
        taskActive = true
        if (currentStart) {
          window.clearTimeout(currentStart.timer)
          const resolve = currentStart.resolve
          currentStart = null
          resolve()
        }
        if (!currentTurn) return
        if (stopRequested || mode.value === 'stopping') {
          // 用户在 ASR 启动完成前就结束了短句：先把预卷/待发音频补上，
          // 否则极短的“好/嗯”会因为握手延迟被整段丢掉。
          flushFrames()
          if (!finishSent && currentSocket.readyState === WebSocket.OPEN) {
            finishSent = true
            currentSocket.send(JSON.stringify({ type: 'stop' }))
          }
        } else {
          mode.value = 'recording'
          status.value = '正在聆听...'
          flushFrames()
        }
      } else if (message.type === 'partial') {
        partialText = message.text || ''
        status.value = '正在识别...'
        updateDraft()
        onTranscript?.({ text: transcript(), partial: partialText, final: false })
      } else if (message.type === 'final') {
        const text = (message.text || '').trim()
        if (text) finalSegments.push(text)
        partialText = ''
        updateDraft()
        onTranscript?.({ text: transcript(), partial: '', final: true })
      } else if (message.type === 'done') {
        if (!currentTurn || mode.value === 'idle' || mode.value === 'monitoring') return
        const serverText = (message.text || '').trim()
        const localText = transcript().trim()
        const text = serverText.length >= localText.length ? serverText : localText
        const wasStopping = currentTurn.stopping
        settleTurn(text)
        // 正常流程由 finishUtterance 统一收尾；这里只处理服务端主动结束的情况。
        if (!wasStopping && currentTurn) void finalizeTurn(text, { emit: currentTurn.emit })
      } else if (message.type === 'error') {
        handleServerError(message.message || '语音识别失败')
      }
    }

    currentSocket.onclose = () => {
      if (socket !== currentSocket) return
      socket = null
      socketPromise = null
      ready = false
      taskActive = false
      if (currentStart) {
        // 启动失败交给 beginUtterance 的 catch 统一收尾并上报。
        failStart(new Error('语音连接已断开'))
        return
      }
      if (mode.value === 'recording' || mode.value === 'connecting' || mode.value === 'stopping') {
        settleTurn('')
        const hadTurn = Boolean(currentTurn)
        void (async () => {
          if (hadTurn && currentTurn) await finalizeTurn('', { emit: false })
          onError?.('语音连接已断开')
        })()
      }
    }
  }

  function ensureSocket() {
    if (socket?.readyState === WebSocket.OPEN) return Promise.resolve(socket)
    if (socketPromise) return socketPromise
    const currentSocket = new WebSocket(asrWsUrl())
    socket = currentSocket
    attachSocketEvents(currentSocket)
    socketPromise = new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        socketPromise = null
        if (socket === currentSocket) socket = null
        try {
          currentSocket.close()
        } catch {}
        reject(new Error('连接实时语音服务超时'))
      }, 8000)
      currentSocket.onopen = () => {
        window.clearTimeout(timer)
        socketPromise = null
        resolve(currentSocket)
      }
      currentSocket.onerror = () => {
        window.clearTimeout(timer)
        socketPromise = null
        if (socket === currentSocket) socket = null
        reject(new Error('无法连接实时语音服务'))
        try {
          currentSocket.close()
        } catch {}
      }
    })
    return socketPromise
  }

  async function startTask() {
    const currentSocket = await ensureSocket()
    if (!currentTurn) throw new Error('当前语音轮次已取消')
    return new Promise((resolve, reject) => {
      finishSent = false
      const start = { resolve, reject, timer: null }
      start.timer = window.setTimeout(() => {
        failStart(new Error('实时语音识别连接超时'), start)
        try {
          currentSocket.close()
        } catch {}
      }, START_TIMEOUT_MS)
      currentStart = start
      try {
        currentSocket.send(JSON.stringify({ type: 'start', language_hints: ['zh', 'ja', 'en'] }))
      } catch (error) {
        failStart(error)
      }
    })
  }

  async function startTaskWithRetry() {
    let lastError = null
    for (let attempt = 0; attempt < START_ATTEMPTS; attempt += 1) {
      try {
        return await startTask()
      } catch (error) {
        lastError = error
        if (!currentTurn || mode.value === 'idle') throw error
        // 上一轮刚 done 时，服务端可能还在清理 relay；短暂退避后重试。
        await delay(140 + attempt * 180)
      }
    }
    throw lastError || new Error('无法启动实时语音识别')
  }

  async function flushCapture() {
    if (!worklet) return
    await Promise.race([
      new Promise((resolve) => {
        flushResolver = resolve
        worklet.port.postMessage({ type: 'flush' })
      }),
      new Promise((resolve) => window.setTimeout(resolve, 250)),
    ])
    flushResolver = null
  }

  async function stopCapture() {
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
    vad = null
    flushResolver = null
  }

  async function startCapture() {
    if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) {
      throw new Error('当前浏览器不支持实时语音输入')
    }
    let stream = null
    let context = null
    try {
      stream = await acquireMicrophone()
      context = new (window.AudioContext || window.webkitAudioContext)()
      await context.audioWorklet.addModule(pcmWorkletUrl)
      await context.resume()
      if (mode.value === 'idle') {
        stream.getTracks().forEach((track) => track.stop())
        await context.close()
        return false
      }
      mediaStream = stream
      audioContext = context
      source = context.createMediaStreamSource(stream)
      vad = new VoiceActivityDetector({ frameMs: FRAME_MS, ...vadOptions })
      vad.setBargeMode(bargeMode)
      worklet = new AudioWorkletNode(context, 'pcm-capture-processor', {
        processorOptions: { targetSampleRate: 16000, frameMs: FRAME_MS },
      })
      silence = context.createGain()
      silence.gain.value = 0
      worklet.port.onmessage = (event) => {
        if (event.data?.type === 'pcm') handleFrame(event.data.data)
        if (event.data?.type === 'flushed' && flushResolver) {
          const resolve = flushResolver
          flushResolver = null
          resolve()
        }
      }
      source.connect(worklet).connect(silence).connect(context.destination)
      return true
    } catch (error) {
      stream?.getTracks().forEach((track) => track.stop())
      try { await context?.close() } catch {}
      mediaStream = null
      audioContext = null
      source = null
      worklet = null
      silence = null
      vad = null
      throw error
    }
  }

  function handleFrame(frame) {
    if (!vad) return
    const rms = pcmRms(frame)
    levelTick += 1
    if (levelTick % 3 === 0) speechLevel.value = Math.min(1, rms / 0.12)

    const event = vad.push(rms)

    if (mode.value === 'monitoring') {
      preRoll.push(frame)
      if (preRoll.length > PRE_ROLL_FRAMES) preRoll.shift()
    }

    if (event?.type === 'speech-start') {
      speechActive.value = true
      onSpeechStart?.()
      if (keepAlive && mode.value === 'monitoring') {
        // preRoll 已包含当前帧；交给 beginUtterance 一次性补发，避免重复。
        const prelude = preRoll
        preRoll = []
        void beginUtterance(prelude)
        return
      }
    } else if (event?.type === 'speech-end') {
      speechActive.value = false
      onSpeechEnd?.(event)
      if (keepAlive && (mode.value === 'recording' || mode.value === 'connecting')) {
        void finishUtterance()
        return
      }
    }

    if (mode.value === 'recording' || mode.value === 'connecting') queueFrame(frame)
  }

  async function beginUtterance(prelude = []) {
    if (currentTurn) return
    if (mode.value === 'idle' || mode.value === 'stopping') return
    currentTurn = createTurn()
    resetTranscript()
    finishSent = false
    stopRequested = false
    taskActive = false
    ready = false
    speechActive.value = true
    pendingFrames = prelude.slice()
    pendingBytes = pendingFrames.reduce((total, frame) => total + frame.byteLength, 0)
    preRoll = []
    mode.value = 'connecting'
    status.value = '正在连接实时识别...'
    try {
      await startTaskWithRetry()
    } catch (error) {
      if (!currentTurn) return
      settleTurn('')
      await finalizeTurn('', { emit: false })
      onError?.(error?.message || '无法启动实时语音识别')
    }
  }

  async function finalizeTurn(text, { emit = true } = {}) {
    if (finalizing) return text
    finalizing = true
    const hadTurn = Boolean(currentTurn)
    currentTurn = null
    // 如果还有启动中的任务，主动失败掉，避免它的超时回调影响下一轮。
    if (currentStart) failStart(new Error('语音轮次已取消'))
    taskActive = false
    ready = false
    stopRequested = false
    finishSent = false
    resetTranscript()
    pendingFrames = []
    pendingBytes = 0
    preRoll = []
    speechActive.value = false
    speechLevel.value = 0
    vad?.reset()
    const monitoring = keepAlive || mode.value === 'monitoring'
    if (monitoring) {
      mode.value = 'monitoring'
      status.value = '正在聆听...'
    } else {
      mode.value = 'idle'
      status.value = ''
    }
    if (!monitoring) await stopCapture()
    finalizing = false
    if (emit && hadTurn) onComplete?.(text)
    return text
  }

  async function finishUtterance({ emit = true } = {}) {
    if (!currentTurn) return ''
    const turn = currentTurn
    turn.emit = emit
    if (!turn.stopping) {
      turn.stopping = true
      stopRequested = true
      if (taskActive && ready) {
        // 把 worklet 里不足一帧的尾部音频也送进去，再结束本轮。
        await flushCapture()
        mode.value = 'stopping'
        status.value = '正在识别...'
        if (socket?.readyState === WebSocket.OPEN) {
          flushFrames()
          if (!finishSent) {
            finishSent = true
            socket.send(JSON.stringify({ type: 'stop' }))
          }
        }
      } else {
        mode.value = 'stopping'
        status.value = '正在识别...'
      }
    }

    let timedOut = false
    const text = await Promise.race([
      turn.promise,
      new Promise((resolve) => {
        window.setTimeout(() => {
          timedOut = true
          resolve(transcript().trim())
        }, DONE_TIMEOUT_MS)
      }),
    ])
    if (timedOut && !turn.settled) {
      // 服务端任务可能卡住；断开连接让下一轮从干净的 session 开始。
      const staleSocket = socket
      socket = null
      socketPromise = null
      try { staleSocket?.close() } catch {}
    }
    if (currentTurn === turn) await finalizeTurn(text, { emit })
    return text
  }

  async function start({ keepAlive: alive = false } = {}) {
    if (mode.value !== 'idle') return
    keepAlive = Boolean(alive)
    mode.value = 'connecting'
    status.value = keepAlive ? '正在开启麦克风...' : '正在连接实时识别...'
    resetTranscript()
    speechActive.value = false
    speechLevel.value = 0
    preRoll = []
    try {
      const captured = await startCapture()
      if (!captured || mode.value === 'idle') return
      if (keepAlive) {
        mode.value = 'monitoring'
        status.value = '正在聆听...'
        return
      }
      await beginUtterance([])
    } catch (error) {
      mode.value = 'idle'
      status.value = ''
      await stopCapture()
      onError?.(error?.message || '无法启动语音输入')
    }
  }

  async function stop() {
    keepAlive = false
    if (currentTurn) {
      await finishUtterance({ emit: false })
    } else {
      mode.value = 'idle'
      status.value = ''
      speechActive.value = false
      await stopCapture()
    }
    // finishUtterance 在 keepAlive=false 时会自己回到 idle；这里兜底清理启动中的采集。
    if (mode.value !== 'idle') {
      mode.value = 'idle'
      status.value = ''
      await stopCapture()
    }
  }

  async function toggle() {
    if (mode.value === 'idle') await start()
    else await stop()
  }

  function setBargeMode(enabled) {
    bargeMode = Boolean(enabled)
    vad?.setBargeMode(bargeMode)
  }

  async function dispose() {
    keepAlive = false
    if (currentTurn) {
      settleTurn(transcript().trim())
      await finalizeTurn('', { emit: false })
    }
    await stopCapture()
    const staleSocket = socket
    socket = null
    socketPromise = null
    ready = false
    taskActive = false
    try { staleSocket?.close() } catch {}
    mode.value = 'idle'
    status.value = ''
    speechActive.value = false
  }

  return {
    mode,
    status,
    draft,
    speechActive,
    speechLevel,
    isActive,
    start,
    stop,
    toggle,
    resetTranscript,
    setBargeMode,
    dispose,
  }
}
