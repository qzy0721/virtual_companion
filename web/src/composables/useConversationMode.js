// 对话模式（无感交流）：常驻麦克风 + 浏览器 AEC + 本地 VAD + 云 ASR。
// 状态机: IDLE -> LISTENING -> THINKING -> SPEAKING -> LISTENING -> ...
//
// 打断采用两段式，避免噪声/AEC 残响把角色误暂停：
// 1) VAD 检测到疑似说话：只压低 TTS 音量（或暂扣待播队列），继续播放；
// 2) ASR 真的识别出非回声文字（或疑似说话持续 1.5s）才暂停 TTS 并接管。
// 暂停后说“继续”或点“继续播放”从暂停点接着听；新问题会取消旧回答。

import { computed, ref, watch } from 'vue'
import { isContinueCommand, isPauseCommand, isBackchannel, isLikelyEcho } from '../speechCommands'

const RETRY_MS = 2000
const NO_TTS_FALLBACK_MS = 1500
// ASR 一直没出字时，持续说话超过该时长也视为真实打断。
const BARGE_CONFIRM_MS = 3500

/**
 * @param {object} deps
 *   asr:      useAsrStream 实例（start/stop/mode/speechActive/resetTranscript/setBargeMode）
 *   tts:      useTtsQueue 实例（active/suspended/ducked/currentText/duck/unduck/interrupt/resume/stop）
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
  let barge = null
  let bargeCommittedFrom = null
  let transcriptTimer = null
  let transcriptCandidate = ''

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

  function clearBargeTimer() {
    if (barge?.timer) window.clearTimeout(barge.timer)
    if (barge) barge.timer = null
  }

  function clearTranscriptTimer() {
    if (transcriptTimer) window.clearTimeout(transcriptTimer)
    transcriptTimer = null
    transcriptCandidate = ''
  }

  function clearBarge() {
    clearBargeTimer()
    clearTranscriptTimer()
    barge = null
    bargeCommittedFrom = null
  }

  function spokenText() {
    return tts.currentText.value || (typeof assistantText === 'function' ? assistantText() : '')
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

  // 类似“恢复播放”后的状态：有队列就是 SPEAKING；还在等生成就回 THINKING。
  function settleAfterResume(resumed) {
    if (!resumed) {
      enterListening()
      return
    }
    if (tts.active.value) {
      state.value = 'SPEAKING'
      return
    }
    state.value = 'THINKING'
    scheduleNoTtsFallback()
  }

  // 第一阶段：VAD 只压低 TTS / 暂扣队列，不改状态、不弹“已暂停”。
  function beginBarge() {
    const from = state.value
    if (from !== 'THINKING' && from !== 'SPEAKING') return
    if (barge || bargeCommittedFrom) return
    const ducked = tts.duck()
    if (!ducked) tts.interrupt()
    barge = {
      from,
      mode: ducked ? 'duck' : 'suspend',
      timer: null,
    }
    barge.timer = window.setTimeout(() => {
      // TTS 已经自然播完、状态也回到聆听时，不必再补一次暂停提示。
      if (state.value === 'LISTENING' && !tts.active.value) {
        barge = null
        return
      }
      commitBarge()
    }, BARGE_CONFIRM_MS)
  }

  // 第二阶段：ASR 确认是用户说话后才真正暂停并接管。
  function commitBarge({ notifyUser = true } = {}) {
    if (!barge) return false
    const from = barge.from
    clearBargeTimer()
    clearTranscriptTimer()
    barge = null
    bargeCommittedFrom = from
    turnSeq += 1
    tts.interrupt()
    state.value = 'LISTENING'
    asr.setBargeMode(false)
    if (notifyUser) {
      notify?.(from === 'SPEAKING' ? '已打断，我在听；说“继续”可以接着听' : '我在听你说')
    }
    return true
  }

  // 误触发/回声/附和：恢复刚才压低或暂扣的播放。
  async function restoreBarge() {
    const from = barge?.from || bargeCommittedFrom
    if (!from) return false
    const mode = barge?.mode
    clearBargeTimer()
    clearTranscriptTimer()
    barge = null
    bargeCommittedFrom = null
    if (mode === 'duck' && !tts.suspended.value) {
      tts.unduck()
      state.value = tts.active.value
        ? 'SPEAKING'
        : (from === 'SPEAKING' ? 'LISTENING' : from)
      return true
    }
    const resumed = await tts.resume()
    if (resumed && tts.active.value) state.value = 'SPEAKING'
    else if (from === 'THINKING') state.value = 'THINKING'
    else state.value = 'LISTENING'
    return true
  }

  async function start() {
    if (state.value !== 'IDLE') return
    clearFallback()
    clearRetry()
    clearBarge()
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
    clearBarge()
    asr.setBargeMode(false)
    try { await asr.stop() } catch { /* 忽略 */ }
    tts.stop()
    cancel?.()
  }

  async function toggle() {
    if (state.value === 'IDLE') await start()
    else await stop()
  }

  function onSpeechStart() {
    beginBarge()
  }

  // ASR 增量结果：先观察一下最新 partial，避免一两个字的口误/残响立刻暂停。
  function onTranscript(payload) {
    if (!barge) return
    const text = String(payload?.text || '').trim()
    if (text.length < 2) {
      if (transcriptTimer) {
        window.clearTimeout(transcriptTimer)
        transcriptTimer = null
      }
      transcriptCandidate = ''
      return
    }
    transcriptCandidate = text
    if (isBackchannel(text) || isLikelyEcho(text, spokenText())) {
      // 最新 partial 变成回声/语气词，撤销还没执行的暂停。
      if (transcriptTimer) {
        window.clearTimeout(transcriptTimer)
        transcriptTimer = null
      }
      return
    }
    if (transcriptTimer) return
    transcriptTimer = window.setTimeout(() => {
      transcriptTimer = null
      if (!barge) return
      const candidate = transcriptCandidate.trim()
      if (candidate.length < 2) return
      if (isBackchannel(candidate) || isLikelyEcho(candidate, spokenText())) return
      if (state.value === 'LISTENING' && !tts.active.value) {
        barge = null
        return
      }
      commitBarge()
    }, 180)
  }

  function onSpeechEnd() {
    // 说话已结束就不要再靠“持续说话”兜底计时器触发暂停；
    // 等 ASR 最终结果决定是真实打断、回声还是空噪声。
    if (barge?.timer) {
      window.clearTimeout(barge.timer)
      barge.timer = null
    }
  }

  async function onAsrComplete(text) {
    if (state.value === 'IDLE') return
    clearFallback()
    clearRetry()
    clearTranscriptTimer()
    const cleaned = (text || '').trim()

    if (!cleaned) {
      if (barge || bargeCommittedFrom) {
        await restoreBarge()
        return
      }
      enterListening()
      return
    }

    // 还没提交的疑似打断，先按最终文本再判一次。
    if (barge) {
      if (cleaned.length < 2 || isLikelyEcho(cleaned, spokenText()) || isBackchannel(cleaned)) {
        await restoreBarge()
        return
      }
      commitBarge()
    } else if (bargeCommittedFrom) {
      if (cleaned.length < 2 || isLikelyEcho(cleaned, spokenText()) || isBackchannel(cleaned)) {
        await restoreBarge()
        return
      }
    }

    if (paused.value) {
      if (isContinueCommand(cleaned)) {
        bargeCommittedFrom = null
        const resumed = await tts.resume()
        settleAfterResume(resumed)
        return
      }
      if (isBackchannel(cleaned)) {
        bargeCommittedFrom = null
        const resumed = await tts.resume()
        settleAfterResume(resumed)
        if (resumed && tts.active.value) notify?.('继续播放')
        return
      }
      if (isPauseCommand(cleaned)) {
        bargeCommittedFrom = null
        enterListening()
        notify?.('已暂停，说“继续”接着听')
        return
      }
      // 说别的内容：视为新问题，取消仍在生成的后半段并丢弃暂停队列。
      bargeCommittedFrom = null
      cancel?.()
      tts.stop()
    } else if (state.value === 'SPEAKING' || state.value === 'THINKING') {
      // VAD 事件缺失时的兜底：ASR 已经确认有用户语音，不能让 TTS 继续盖住。
      cancel?.()
      tts.stop()
      state.value = 'LISTENING'
      asr.setBargeMode(false)
    }

    bargeCommittedFrom = null
    const token = ++turnSeq
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
    if (barge || bargeCommittedFrom) void restoreBarge()
    const detail = String(message || '未知错误')
    if (/权限|NotAllowed|Permission|麦克风/i.test(detail)) {
      notify?.(`识别失败：${detail}。请允许浏览器使用麦克风后重新开启对话模式`)
      void stop()
      return
    }
    notify?.(`识别失败：${detail}，2秒后重试...`)
    if (!tts.active.value) {
      enterListening()
      scheduleRestart(RETRY_MS)
    }
  }

  async function resumeSpeech() {
    if (state.value === 'IDLE' || !tts.suspended.value) return
    if (asr.speechActive.value || asr.mode.value === 'recording' || asr.mode.value === 'connecting' || asr.mode.value === 'stopping') {
      notify?.('正在识别刚才的话，请稍等再继续播放')
      return
    }
    clearFallback()
    clearBarge()
    const resumed = await tts.resume()
    settleAfterResume(resumed)
  }

  // TTS 开始/结束驱动 THINKING <-> SPEAKING <-> LISTENING。
  watch(tts.active, (isActive, wasActive) => {
    if (isActive && state.value === 'THINKING' && !barge && !bargeCommittedFrom) {
      clearFallback()
      state.value = 'SPEAKING'
    }
    if (!isActive && wasActive && state.value === 'SPEAKING') {
      enterListening()
    }
  })

  // 角色说话/思考时使用更严格的 VAD 门限，降低 AEC 残留和噪声误触发。
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
    onTranscript,
    onAsrComplete,
    onAsrError,
    resumeSpeech,
  }
}
