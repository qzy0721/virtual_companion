// 对话模式（无感交流）：常驻麦克风 + 浏览器 AEC + 本地 VAD + 云 ASR。
// 状态机: IDLE -> LISTENING -> THINKING -> SPEAKING -> LISTENING -> ...
// 角色说话时 VAD 仍在工作，用户开口即打断；被打断的语音停在当前位置，
// 说“继续”或点“继续播放”可以接着听。

import { computed, ref, watch } from 'vue'
import { isContinueCommand, isPauseCommand, isBackchannel, isLikelyEcho } from '../speechCommands'

const RETRY_MS = 2000
const NO_TTS_FALLBACK_MS = 1500

/**
 * @param {object} deps
 *   asr:      useAsrStream 实例（start/stop/mode/resetTranscript/setBargeMode）
 *   tts:      useTtsQueue 实例（active/suspended/interrupt/resume/stop）
 *   sendText: async (text) => void  —— 发送一轮对话（App.vue 的 sendChatText 管线）
 *   cancel:   () => void            —— 取消正在进行的生成
 *   notify:   (text) => void        —— 状态提示
 *   assistantText: () => string     —— 当前角色已生成/正在说的文本（回声兜底）
 */
export function useConversationMode({ asr, tts, sendText, cancel, notify, assistantText }) {
  const state = ref('IDLE') // IDLE / LISTENING / THINKING / SPEAKING
  const active = computed(() => state.value !== 'IDLE')
  const paused = computed(() => tts.suspended.value)
  // 等本轮 ASR 真正收尾（回到 monitoring）后再允许继续，避免暂停指令还没
  // 识别完就被下一次说话抢跑。
  const canResume = computed(() => (
    paused.value
    && !asr.speechActive.value
    && (asr.mode.value === 'monitoring' || asr.mode.value === 'idle')
  ))
  let fallbackTimer = null
  let retryTimer = null
  let turnSeq = 0

  const statusText = computed(() => {
    if (state.value === 'IDLE') return ''
    if (paused.value) {
      return asr.speechActive.value
        ? '正在听你说...（说“继续”可接着播放）'
        : '已暂停，说“继续”或点“继续播放”接着听'
    }
    switch (state.value) {
      case 'LISTENING': return '正在听你说话...'
      case 'THINKING': return '思考中...'
      case 'SPEAKING': return '角色说话中...（可直接开口打断）'
      default: return ''
    }
  })

  function clearFallback() {
    if (fallbackTimer) window.clearTimeout(fallbackTimer)
    fallbackTimer = null
  }

  function clearRetry() {
    if (retryTimer) window.clearTimeout(retryTimer)
    retryTimer = null
  }

  function scheduleRestart(delay = 0) {
    clearRetry()
    retryTimer = window.setTimeout(async () => {
      retryTimer = null
      if (state.value === 'IDLE' || asr.mode.value !== 'idle') return
      await asr.start({ keepAlive: true })
    }, delay)
  }

  function enterListening() {
    if (state.value === 'IDLE') return
    state.value = 'LISTENING'
    asr.setBargeMode(false)
    asr.resetTranscript()
    if (asr.mode.value === 'idle') scheduleRestart(0)
  }

  function scheduleNoTtsFallback() {
    clearFallback()
    fallbackTimer = window.setTimeout(() => {
      fallbackTimer = null
      // 角色没有配置音色时不会有 TTS 任务，不能让状态卡在 THINKING。
      if (state.value === 'THINKING' && !tts.active.value) enterListening()
    }, NO_TTS_FALLBACK_MS)
  }

  async function start() {
    if (state.value !== 'IDLE') return
    clearFallback()
    clearRetry()
    tts.stop()
    cancel?.()
    state.value = 'LISTENING'
    asr.setBargeMode(false)
    await asr.start({ keepAlive: true })
  }

  async function stop() {
    state.value = 'IDLE'
    turnSeq += 1
    clearFallback()
    clearRetry()
    asr.setBargeMode(false)
    try { await asr.stop() } catch { /* 忽略 */ }
    tts.stop()
    cancel?.()
  }

  async function toggle() {
    if (state.value === 'IDLE') await start()
    else await stop()
  }

  // 用户开口：角色正在思考/说话时立即暂停 TTS。
  // 这里先不取消生成，等识别结果确认是新问题后再取消；否则“暂停→继续”
  // 只能续上已经生成的句子，后半段回答会永久丢失。
  function onSpeechStart() {
    // 用户一开口就让仍在等待的旧回合失效，避免它稍后把新回合的状态改回 SPEAKING。
    turnSeq += 1
    if (state.value !== 'THINKING' && state.value !== 'SPEAKING') return
    const wasSpeaking = state.value === 'SPEAKING'
    const interrupted = tts.interrupt()
    state.value = 'LISTENING'
    asr.setBargeMode(false)
    if (interrupted) {
      notify?.(wasSpeaking ? '已打断，我在听；说“继续”可以接着听' : '已暂停，我在听')
    } else {
      notify?.('我在听你说')
    }
  }

  function onSpeechEnd() {
    // 端点由 useAsrStream 的本地静音判据直接触发，这里只保留扩展点。
  }

  async function onAsrComplete(text) {
    if (state.value === 'IDLE') return
    const token = ++turnSeq
    clearFallback()
    clearRetry()
    const cleaned = (text || '').trim()
    if (!cleaned) {
      // 只是噪声/没听清：把刚才暂停的播放恢复，避免角色被无声打断后卡住。
      if (paused.value) {
        state.value = 'SPEAKING'
        const resumed = await tts.resume()
        if (resumed) notify?.('没听清，继续播放')
        else enterListening()
      } else {
        enterListening()
      }
      return
    }

    const spoken = typeof assistantText === 'function' ? assistantText() : ''
    // 只在打断后的暂停态做回声判断：正常聆听时用户复述角色台词不应被吞掉。
    if (paused.value && isLikelyEcho(cleaned, spoken)) {
      state.value = 'SPEAKING'
      const resumed = await tts.resume()
      if (!resumed) enterListening()
      else notify?.('检测到扬声器回声，已继续播放')
      return
    }

    if (paused.value) {
      if (isContinueCommand(cleaned)) {
        state.value = 'SPEAKING'
        const resumed = await tts.resume()
        if (!resumed) enterListening()
        return
      }
      if (isBackchannel(cleaned)) {
        // “嗯/哦/好的”只是附和：继续播放，不要打断成新问题。
        state.value = 'SPEAKING'
        const resumed = await tts.resume()
        if (resumed) notify?.('继续播放')
        else enterListening()
        return
      }
      if (isPauseCommand(cleaned)) {
        enterListening()
        notify?.('已暂停，说“继续”接着听')
        return
      }
      // 说别的内容：视为新问题，取消仍在生成的后半段并丢弃暂停队列。
      cancel?.()
      tts.stop()
    }

    state.value = 'THINKING'
    asr.setBargeMode(true)
    try {
      await sendText(cleaned)
      if (token !== turnSeq) return
      if (state.value === 'THINKING') state.value = 'SPEAKING'
      scheduleNoTtsFallback()
    } catch (error) {
      if (token !== turnSeq) return
      notify?.(`对话失败：${error?.message || '未知错误'}，2秒后重试...`)
      enterListening()
      scheduleRestart(RETRY_MS)
    }
  }

  function onAsrError(message) {
    if (state.value === 'IDLE') return
    const detail = String(message || '未知错误')
    if (/权限|NotAllowed|Permission|麦克风/i.test(detail)) {
      notify?.(`识别失败：${detail}。请允许浏览器使用麦克风后重新开启对话模式`)
      void stop()
      return
    }
    notify?.(`识别失败：${detail}，2秒后重试...`)
    enterListening()
    scheduleRestart(RETRY_MS)
  }

  async function resumeSpeech() {
    if (state.value === 'IDLE' || !tts.suspended.value) return
    if (asr.speechActive.value || asr.mode.value === 'recording' || asr.mode.value === 'connecting' || asr.mode.value === 'stopping') {
      notify?.('正在识别刚才的话，请稍等再继续播放')
      return
    }
    cancel?.()
    clearFallback()
    state.value = 'SPEAKING'
    const resumed = await tts.resume()
    if (!resumed) enterListening()
  }

  // TTS 开始/结束驱动 THINKING <-> SPEAKING <-> LISTENING。
  watch(tts.active, (isActive, wasActive) => {
    if (isActive && state.value === 'THINKING') {
      clearFallback()
      state.value = 'SPEAKING'
    }
    if (!isActive && wasActive && state.value === 'SPEAKING') {
      enterListening()
    }
  })

  // 角色说话时使用更严格的 VAD 门限，降低 AEC 残留和噪声误触发。
  watch(state, (value) => {
    asr.setBargeMode(value === 'THINKING' || value === 'SPEAKING')
  })

  return {
    state,
    active,
    paused,
    canResume,
    statusText,
    start,
    stop,
    toggle,
    onSpeechStart,
    onSpeechEnd,
    onAsrComplete,
    onAsrError,
    resumeSpeech,
  }
}
