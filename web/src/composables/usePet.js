// 桌宠控制 composable（融合自 AI 虚拟人助手项目的桌宠弹出/收回与消息路由）
// 核心思路：提供一个与 CompanionStage 同签名的 driver 对象，
// Phase 1 中 App.vue 将本地舞台和桌宠 driver 组合为广播 driver。

import { computed, onBeforeUnmount, ref } from 'vue'
import { apiUrl } from '../api'
import { getThemePreference } from './useTheme'

const petAlive = ref(false)
const petReady = ref(false)
const petAvailable = ref(true)
let _pollTimer = null
let _volLastTime = 0
let _roleName = ''

// 桌宠模型的表情/动作清单缓存（模型在桌宠里，主窗口无法直接读取）
let _capabilities = { expressions: [], actions: [] }

async function _post(path, body) {
  try {
    const resp = await fetch(apiUrl(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    })
    return resp.ok
  } catch {
    return false
  }
}

async function _refreshCapabilities() {
  try {
    const resp = await fetch(apiUrl('/api/pet/capabilities'))
    if (resp.ok) _capabilities = await resp.json()
  } catch { /* 静默 */ }
}

async function _pollStatus() {
  try {
    const resp = await fetch(apiUrl('/api/pet/status'))
    if (resp.ok) {
      const data = await resp.json()
      const wasReady = petReady.value
      petAlive.value = Boolean(data.alive)
      petReady.value = Boolean(data.ready)
      petAvailable.value = data.available !== false
      // 桌宠刚就绪：拉取一次 capabilities（表情分析需要模型真实表情表）
      if (!wasReady && petReady.value) _refreshCapabilities()
      return
    }
  } catch { /* 后端不可达 */ }
  petAlive.value = false
  petReady.value = false
}

export function usePet() {
  function startPolling() {
    if (_pollTimer) return
    _pollStatus()
    _pollTimer = setInterval(_pollStatus, 1000)
  }

  function stopPolling() {
    if (_pollTimer) {
      clearInterval(_pollTimer)
      _pollTimer = null
    }
  }

  async function setRole(roleName) {
    _roleName = String(roleName || '').trim()
    await _post('/api/pet/theme', { theme: getThemePreference() })
    return _post('/api/pet/context', { role_name: _roleName })
  }

  async function openPet(roleName = _roleName) {
    if (roleName) await setRole(roleName)
    const ok = await _post('/api/pet/open', {})
    if (ok) {
      _pollStatus()
    }
    return ok
  }

  async function closePet() {
    const ok = await _post('/api/pet/close')
    petAlive.value = false
    petReady.value = false
    return ok
  }

  // 驱动消息转发（主窗口 -> 后端 -> 桌宠页）
  function drive(data) {
    return _post('/api/pet/drive', data)
  }

  // 与 CompanionStage 同签名的 driver：useTtsQueue 的全部 stage() 调用路由到桌宠
  const petDriver = {
    setExpression: (name, intensity) => drive({ type: 'expression', name, intensity }),
    clearExpression: () => drive({ type: 'reset' }),
    setMouthOpen: (value) => {
      // 音量消息节流：最多每 150ms 一次（避免 HTTP 拥堵）
      const now = Date.now()
      if (now - _volLastTime < 150 && value > 0.02) return
      _volLastTime = now
      drive({ type: 'volume', volume: value })
    },
    playAction: (name) => drive({ type: 'animation', name }),
    stopAction: () => drive({ type: 'animation', name: 'none' }),
    getExpressions: () => _capabilities.expressions || [],
    getActions: () => (_capabilities.actions && _capabilities.actions.length ? _capabilities.actions : ['none']),
    // 桌宠侧模型切换（与本地舞台 loadUrl 同签名）
    loadUrl: (url) => drive({ type: 'load_model', url }),
  }

  // 模型同步：本地舞台与桌宠同时更新（桌宠未开则仅缓存到后端）
  async function syncModel(url) {
    return drive({ type: 'load_model', url })
  }

  const petActive = computed(() => petAlive.value && petReady.value)

  onBeforeUnmount(stopPolling)

  return {
    petAlive, petReady, petActive, petAvailable,
    startPolling, stopPolling, openPet, closePet, setRole, drive, syncModel, petDriver,
  }
}
