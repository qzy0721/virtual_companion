// 对话模式（无感交流，融合自 AI 虚拟人助手项目 app.py 的对话模式 JS）
// 状态机: IDLE -> LISTENING -> THINKING -> SPEAKING -> LISTENING -> ...
// 基于实时 ASR（WebSocket）+ SSE 对话 + TTS 队列，全程无需按键。

import { computed, ref, watch } from 'vue'

const SILENCE_TIMEOUT_MS = 1500   // 识别文本静默多久后认为本轮说完
const RESTART_DELAY_MS = 400      // 角色说完后延迟多久重新录音（避免录入扬声器回声）
const ERROR_RETRY_MS = 2000       // 出错后重试延迟

/**
 * @param {object} deps
 *   asr:      useAsrStream 实例（start/stop/mode/draft）
 *   tts:      useTtsQueue 实例（active/stop）
 *   sendText: async (text) => void  —— 发送一轮对话（App.vue 的 sendMessage 管线）
 *   cancel:   () => void            —— 取消正在进行的生成
 *   notify:   (text) => void        —— 状态提示（system 消息）
 */
export function useConversationMode({ asr, tts, sendText, cancel, notify }) {
  const state = ref('IDLE') // IDLE / LISTENING / THINKING / SPEAKING
  const active = computed(() => state.value !== 'IDLE')

  let _silenceTimer = null
  let _fallbackTimer = null
  let _restartTimer = null

  const statusText = computed(() => {
    switch (state.value) {
      case 'LISTENING': return ' 正在听你说话...'
      case 'THINKING': return ' 思考中...'
      case 'SPEAKING': return ' 角色说话中...'
      default: return ''
    }
  })

  function _clearTimers() {
    if (_silenceTimer) { clearTimeout(_silenceTimer); _silenceTimer = null }
    if (_fallbackTimer) { clearTimeout(_fallbackTimer); _fallbackTimer = null }
    if (_restartTimer) { clearTimeout(_restartTimer); _restartTimer = null }
  }

  async function start() {
    if (state.value !== 'IDLE') return
    tts.stop()
    cancel?.()
    state.value = 'LISTENING'
    await asr.start()
  }

  async function stop() {
    state.value = 'IDLE'
    _clearTimers()
    try { await asr.stop() } catch { /* 忽略 */ }
    tts.stop()
    cancel?.()
  }

  async function toggle() {
    if (state.value === 'IDLE') await start()
    else await stop()
  }

  // ---- 静音自动结束：识别文本产生后，持续静默即认为本轮说完 ----
  watch(asr.draft, (text) => {
    if (state.value !== 'LISTENING') return
    if (!text || !text.trim()) return
    if (_silenceTimer) clearTimeout(_silenceTimer)
    _silenceTimer = setTimeout(async () => {
      if (state.value === 'LISTENING') {
        console.log('[Conv] 检测到静默，自动结束本轮录音')
        await asr.stop()
      }
    }, SILENCE_TIMEOUT_MS)
  })

  // ---- ASR 一轮识别完成（由 App.vue 的 onComplete 路由进来） ----
  async function onAsrComplete(text) {
    if (state.value === 'IDLE') return
    const cleaned = (text || '').trim()
    if (!cleaned) {
      // 没听清：直接重新录音
      await backToListening(0)
      return
    }
    state.value = 'THINKING'
    try {
      await sendText(cleaned)
      // 对话完成 -> 等待 TTS 播完（tts.active 的 watcher 接管回到 LISTENING）
      state.value = 'SPEAKING'
      // 兜底：角色无音色（无 TTS 任务）时，短暂等待后直接重新录音
      _fallbackTimer = setTimeout(() => {
        if (state.value === 'SPEAKING' && !tts.active.value) backToListening(RESTART_DELAY_MS)
      }, 1200)
    } catch (error) {
      notify?.(`对话失败：${error?.message || '未知错误'}，2秒后重试...`)
      backToListening(ERROR_RETRY_MS)
    }
  }

  // ---- TTS 播放结束 -> 恢复监听 ----
  watch(tts.active, (isActive, wasActive) => {
    if (state.value === 'SPEAKING' && wasActive && !isActive) {
      backToListening(RESTART_DELAY_MS)
    }
  })

  async function backToListening(delayMs = 0) {
    if (state.value === 'IDLE') return
    if (_restartTimer) clearTimeout(_restartTimer)
    _restartTimer = setTimeout(async () => {
      if (state.value === 'IDLE' || asr.mode.value !== 'idle') return
      state.value = 'LISTENING'
      await asr.start()
    }, delayMs)
  }

  // ASR 错误（由 App.vue 的 onError 路由进来）
  function onAsrError(message) {
    if (state.value === 'IDLE') return
    notify?.(`识别失败：${message}，2秒后重试...`)
    backToListening(ERROR_RETRY_MS)
  }

  return { state, active, statusText, start, stop, toggle, onAsrComplete, onAsrError }
}
