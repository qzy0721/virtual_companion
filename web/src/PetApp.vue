<script setup>
// CEF 桌宠页：只承载 VRM 与无感语音对话，不复制主窗口的文本聊天 UI。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { apiUrl } from './api'
import { guessExpression } from './expression'
import CompanionStage from './components/CompanionStage.vue'
import { useAsrStream } from './composables/useAsrStream'
import { useChatStream } from './composables/useChatStream'
import { useConversationMode } from './composables/useConversationMode'
import { useTtsQueue } from './composables/useTtsQueue'

const stage = ref(null)
const roleName = ref('')
const menuOpen = ref(false)
const menuX = ref(0)
const menuY = ref(0)
const notice = ref('')
let noticeTimer = null
let conversationApi = null
let _currentModelUrl = ''
const PET_THEMES = new Set(['wisteria', 'hatsune', 'sakura', 'matcha', 'starry'])

function applyPetTheme(theme) {
  const palette = PET_THEMES.has(theme) ? theme : 'wisteria'
  document.documentElement.dataset.theme = palette === 'starry' ? 'dark' : 'light'
  document.documentElement.dataset.palette = palette
}

window.setPetTheme = applyPetTheme

const tts = useTtsQueue(stage)
const chat = useChatStream()
const assistantSpeechText = ref('')
const asr = useAsrStream({
  onComplete: (text) => conversationApi?.onAsrComplete(text),
  onError: (message) => conversationApi?.onAsrError(message),
  onSpeechStart: () => conversationApi?.onSpeechStart(),
  onSpeechEnd: (event) => conversationApi?.onSpeechEnd(event),
})

function notify(message) {
  notice.value = String(message || '')
  if (noticeTimer) window.clearTimeout(noticeTimer)
  if (notice.value) noticeTimer = window.setTimeout(() => { notice.value = '' }, 4200)
}

function cancelGeneration() {
  chat.cancel()
}

async function fetchExpressions(sentences) {
  if (!sentences.length) return []
  try {
    const response = await fetch(apiUrl('/api/chat/expressions'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sentences,
        available_expressions: stage.value?.getExpressions() || ['happy', 'sad', 'angry', 'surprised', 'relaxed', 'neutral'],
        available_actions: stage.value?.getActions() || ['none'],
      }),
    })
    if (!response.ok) return []
    return (await response.json()).expressions || []
  } catch {
    return []
  }
}

async function sendChatText(text) {
  const message = String(text || '').trim()
  if (!message) return
  if (!roleName.value) throw new Error('尚未选择角色')

  await tts.ensureAudioReady()
  const sentences = []
  let chatError = ''
  const completed = await chat.send({
    roleName: roleName.value,
    message,
    onStart: () => {
      tts.stop()
      assistantSpeechText.value = ''
    },
    onSentence: async (sentence) => {
      const expression = guessExpression(sentence)
      sentences.push(sentence)
      assistantSpeechText.value += sentence
      await tts.enqueue({ text: sentence, roleName: roleName.value, ...expression })
    },
    onDone: async () => {
      const expressions = await fetchExpressions(sentences)
      for (const item of expressions) {
        const index = item.sentence_index
        if (Number.isInteger(index) && index >= 0 && index < sentences.length) tts.patch(index, item)
      }
    },
    onError: (messageText) => { chatError = messageText || '对话失败' },
  })
  if (!completed || chatError) throw new Error(chatError || '对话已中断')
}

const conversation = useConversationMode({
  asr,
  tts,
  sendText: sendChatText,
  cancel: cancelGeneration,
  notify,
  assistantText: () => assistantSpeechText.value,
})
conversationApi = conversation

const conversationLabel = computed(() => (
  conversation.active.value ? '停止对话模式' : '开始对话模式'
))

async function loadRoleContext() {
  try {
    const contextResponse = await fetch(apiUrl('/api/pet/context'))
    if (contextResponse.ok) {
      const context = await contextResponse.json()
      roleName.value = String(context.role_name || '')
      applyPetTheme(String(context.theme || 'wisteria'))
    }
    if (roleName.value) return

    // 独立运行 pet_window.py 时没有主窗口同步角色，回退到角色列表第一项。
    const rolesResponse = await fetch(apiUrl('/api/roles'))
    if (rolesResponse.ok) roleName.value = String((await rolesResponse.json()).roles?.[0] || '')
  } catch {
    roleName.value = ''
  }
}

async function toggleConversation() {
  if (!roleName.value) {
    notify('没有可用角色，请先在主界面创建或选择角色')
    return
  }
  try {
    // 在菜单点击这一用户手势中预先解锁音频上下文。
    await tts.ensureAudioReady()
    await conversation.toggle()
  } catch (error) {
    notify(`无法启动对话模式：${error?.message || '未知错误'}`)
  }
}

function openContextMenu(event) {
  event.preventDefault()
  const width = 168
  const height = 172
  const edge = 10
  const gap = 10
  // 菜单优先出现在点击位置的外侧；窗口加宽后，模型左右均留有菜单空间。
  // 若靠近边缘则自动翻转，避免菜单回压到模型上。
  const rightX = event.clientX + gap
  const leftX = event.clientX - width - gap
  const preferredX = event.clientX < window.innerWidth / 2 ? leftX : rightX
  const fallbackX = event.clientX < window.innerWidth / 2 ? rightX : leftX
  const fits = (x) => x >= edge && x + width <= window.innerWidth - edge
  const targetX = fits(preferredX) ? preferredX : (fits(fallbackX) ? fallbackX : preferredX)
  menuX.value = Math.max(edge, Math.min(targetX, window.innerWidth - width - edge))
  menuY.value = Math.max(32, Math.min(event.clientY, window.innerHeight - height - 8))
  menuOpen.value = true
}

function onMenuPointerDown(event) {
  event.stopPropagation()
  if (!event.target.closest('button')) menuOpen.value = false
}

function post(path, body) {
  return fetch(apiUrl(path), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  }).catch(() => {})
}

function onStageReady(payload) {
  post('/api/pet/ready', payload)
}

async function quitPet() {
  menuOpen.value = false
  await conversation.stop()
  const response = await post('/api/pet/quit')
  if (!response?.ok) notify('桌宠退出失败')
}

async function openMainWindow() {
  const response = await post('/api/shell/show-main')
  if (!response?.ok) {
    notify('主窗口控制通道不可用')
    return
  }
  menuOpen.value = false
}

// 主窗口仍可驱动桌宠；桌宠自身对话时复用同一个舞台 driver。
window.receiveFromMain = function (data) {
  const target = stage.value
  if (!target || !data?.type) return
  switch (data.type) {
    case 'expression': target.setExpression(data.name, data.intensity ?? 0.8); break
    case 'volume': target.setMouthOpen(data.volume ?? 0); break
    case 'animation':
      if (data.name && data.name !== 'none') target.playAction(data.name)
      else target.stopAction()
      break
    case 'reset':
      target.clearExpression()
      target.setMouthOpen(0)
      target.stopAction()
      break
    case 'load_model':
      if (data.url && data.url !== _currentModelUrl) {
        _currentModelUrl = data.url
        target.loadUrl(data.url)
      }
      break
    case 'idle_action':
      target.setIdleAction(data.name || 'idle')
      break
  }
}

onMounted(loadRoleContext)

onBeforeUnmount(async () => {
  if (noticeTimer) window.clearTimeout(noticeTimer)
  await conversation.stop()
  await asr.dispose()
  await tts.dispose()
  chat.cancel()
  delete window.setPetTheme
})
</script>

<template>
  <div class="pet-shell" @contextmenu="openContextMenu" @pointerdown.left="menuOpen = false">
    <div class="pet-drag-bar"></div>
    <div class="pet-title">桌宠 · 拖拽移动</div>
    <CompanionStage ref="stage" :pet="true" @ready="onStageReady" />

    <div
      v-if="menuOpen"
      class="pet-context-menu"
      :style="{ left: `${menuX}px`, top: `${menuY}px` }"
      @pointerdown="onMenuPointerDown"
      @contextmenu.prevent
    >
      <div class="pet-context-role">{{ roleName || '未选择角色' }}</div>
      <button type="button" :class="{ active: conversation.active.value }" @click="toggleConversation">
        {{ conversationLabel }}
      </button>
      <button class="pet-show-main-button" type="button" @click="openMainWindow">打开主窗口</button>
      <button class="pet-quit-button" type="button" @click="quitPet">退出桌宠</button>
      <div class="pet-context-status">{{ conversation.statusText.value || asr.status.value || '右键菜单可随时停止' }}</div>
    </div>

    <div v-if="notice" class="pet-notice">{{ notice }}</div>
  </div>
</template>
