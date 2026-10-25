import { computed, ref } from 'vue'
import { apiUrl } from '../api'

export function useTtsQueue(stageRef) {
  const status = ref('就绪')
  const queueLength = ref(0)
  // 对话模式需要知道"合成+播放是否全部结束"（active 为其循环依据）
  const playing = ref(false)
  const pendingCount = ref(0)
  // 被用户打断后，队列保持在当前位置；resume 可以从暂停点继续。
  const suspended = ref(false)
  let audioContext = null
  let analyser = null
  let analyserConnected = false
  let audioData = null
  let currentAudio = null
  let currentSource = null
  let currentItem = null
  let mouthTimer = null
  let audioQueue = []
  let pending = []
  let inFlight = []
  let busy = false
  let nextIndex = 0
  let generation = 0

  function stage() {
    return stageRef.value
  }

  function updateQueueLength() {
    queueLength.value = audioQueue.length
    // 当前正在播放/暂停的句子也算未完成，否则打断后 active 会误判为 false。
    pendingCount.value = pending.length + inFlight.length + audioQueue.length + (currentItem ? 1 : 0)
  }

  async function ensureAudioReady() {
    if (!audioContext) {
      audioContext = new (window.AudioContext || window.webkitAudioContext)()
      analyser = audioContext.createAnalyser()
      analyser.fftSize = 256
      analyser.smoothingTimeConstant = 0.3
      audioData = new Uint8Array(analyser.fftSize)
    }
    if (audioContext.state === 'suspended') await audioContext.resume()
  }

  function volume() {
    if (!analyser || !audioData) return 0
    analyser.getByteTimeDomainData(audioData)
    let sum = 0
    for (const value of audioData) {
      const normalized = (value - 128) / 128
      sum += normalized * normalized
    }
    return Math.sqrt(sum / audioData.length)
  }

  function stopMouthLoop() {
    if (mouthTimer) window.clearInterval(mouthTimer)
    mouthTimer = null
  }

  function startMouthLoop(audio) {
    stopMouthLoop()
    if (!audioContext || !analyser) return
    mouthTimer = window.setInterval(() => {
      if (!audio.paused) stage()?.setMouthOpen(Math.min(volume() * 3.5, 1))
    }, 50)
  }

  async function fetchTts(text, roleName) {
    const response = await fetch(apiUrl('/api/tts'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, role_name: roleName }),
    })
    if (!response.ok) {
      const body = await response.json().catch(() => ({}))
      throw new Error(body.detail || '语音合成失败')
    }
    const data = await response.json()
    return { url: apiUrl(data.url), durationMs: data.duration_ms }
  }

  async function enqueue({ text, roleName, expression, intensity, action }) {
    const index = nextIndex++
    pending.push({ text, roleName, expression, intensity, action, index })
    process()
    return index
  }

  async function process() {
    if (busy) return
    const activeGeneration = generation
    busy = true
    try {
      while (pending.length && activeGeneration === generation) {
        const pendingItem = pending.shift()
        const index = pendingItem.index
        if (!suspended.value) status.value = `合成 ${index + 1}...`
        let result
        inFlight.push(pendingItem)
        try {
          result = await fetchTts(pendingItem.text, pendingItem.roleName)
        } catch (error) {
          status.value = error.message || '语音合成失败'
          continue
        } finally {
          const inFlightIndex = inFlight.indexOf(pendingItem)
          if (inFlightIndex >= 0) inFlight.splice(inFlightIndex, 1)
        }
        if (activeGeneration !== generation) return
        audioQueue.push({ ...pendingItem, ...result })
        updateQueueLength()
        if (!playing.value && !suspended.value) playNext()
      }
    } finally {
      if (activeGeneration === generation) {
        busy = false
      }
    }
  }

  function playNext() {
    if (suspended.value) {
      playing.value = false
      return
    }
    if (!audioQueue.length) {
      playing.value = false
      currentItem = null
      status.value = '就绪'
      updateQueueLength()
      return
    }

    playing.value = true
    currentItem = audioQueue.shift()
    updateQueueLength()
    if (currentItem.expression) stage()?.setExpression(currentItem.expression, currentItem.intensity)
    if (currentItem.action && currentItem.action !== 'none') stage()?.playAction(currentItem.action)
    else stage()?.stopAction()

    status.value = `播放 ${currentItem.index + 1}`
    const audio = new Audio(currentItem.url)
    audio.crossOrigin = 'anonymous'
    currentAudio = audio

    if (audioContext && analyser) {
      const source = audioContext.createMediaElementSource(audio)
      source.connect(analyser)
      if (!analyserConnected) {
        analyser.connect(audioContext.destination)
        analyserConnected = true
      }
      currentSource = source
      startMouthLoop(audio)
    }

    const playingItem = currentItem
    let finished = false
    const finish = () => {
      if (finished) return
      finished = true
      stopMouthLoop()
      try { currentSource?.disconnect() } catch {}
      currentSource = null
      currentAudio = null
      if (currentItem === playingItem) currentItem = null
      stage()?.setMouthOpen(0)
      stage()?.clearExpression()
      stage()?.stopAction()
      playing.value = false
      updateQueueLength()
      if (!suspended.value) playNext()
    }
    audio.onended = finish
    audio.onerror = finish
    audio.play().catch(finish)
  }

  function patch(index, expression) {
    for (const item of pending) {
      if (item.index === index) Object.assign(item, expression)
    }
    for (const item of inFlight) {
      if (item.index === index) Object.assign(item, expression)
    }
    for (const item of audioQueue) {
      if (item.index === index) Object.assign(item, expression)
    }
    if (currentItem?.index === index && currentAudio) {
      const previousAction = currentItem.action || 'none'
      Object.assign(currentItem, expression)
      stage()?.setExpression(currentItem.expression, currentItem.intensity)
      const nextAction = currentItem.action || 'none'
      if (nextAction !== previousAction) {
        if (nextAction !== 'none') stage()?.playAction(nextAction)
        else stage()?.stopAction()
      }
    }
  }

  // 打断：立刻静音并保留当前句的播放位置和后续队列，等待“继续”。
  // 即使还没有音频（角色仍在思考），也会进入暂停态，避免新一轮生成突然开口。
  function interrupt() {
    const hasWork = playing.value || pendingCount.value > 0 || Boolean(currentItem)
    if (suspended.value) return hasWork
    suspended.value = true
    if (currentAudio) {
      try { currentAudio.pause() } catch {}
      stage()?.setMouthOpen(0)
    }
    stopMouthLoop()
    playing.value = false
    status.value = hasWork ? '已暂停' : '等待播放'
    updateQueueLength()
    return hasWork
  }

  async function resume() {
    if (!suspended.value) return false
    await ensureAudioReady()
    suspended.value = false
    if (currentAudio && currentItem) {
      try {
        await currentAudio.play()
        playing.value = true
        startMouthLoop(currentAudio)
        status.value = `播放 ${currentItem.index + 1}`
        updateQueueLength()
        return true
      } catch {
        playing.value = false
      }
    }
    playNext()
    return true
  }

  function stop() {
    generation += 1
    suspended.value = false
    stopMouthLoop()
    currentAudio?.pause()
    if (currentAudio) currentAudio.currentTime = 0
    try { currentSource?.disconnect() } catch {}
    currentAudio = null
    currentSource = null
    currentItem = null
    audioQueue = []
    pending = []
    inFlight = []
    busy = false
    playing.value = false
    nextIndex = 0
    status.value = '就绪'
    updateQueueLength()
    stage()?.setMouthOpen(0)
    stage()?.clearExpression()
    stage()?.stopAction()
  }

  async function dispose() {
    stop()
    await audioContext?.close()
    audioContext = null
  }

  // active: 有待合成/待播放/播放中/暂停中的任何一项即为 true（对话模式循环依据）
  const active = computed(() => playing.value || pendingCount.value > 0)

  return {
    status,
    queueLength,
    playing,
    pendingCount,
    active,
    suspended,
    ensureAudioReady,
    enqueue,
    patch,
    interrupt,
    resume,
    stop,
    dispose,
  }
}
