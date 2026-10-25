<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { apiUrl } from './api'
import { guessExpression } from './expression'
import ChatPanel from './components/ChatPanel.vue'
import CompanionStage from './components/CompanionStage.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import MemoryPanel from './components/MemoryPanel.vue'
import RoleSidebar from './components/RoleSidebar.vue'
import TitleBar from './components/TitleBar.vue'
import { openConfirm } from './composables/useConfirm'
import { useAsrStream } from './composables/useAsrStream'
import { useChatStream } from './composables/useChatStream'
import { useConversationMode } from './composables/useConversationMode'
import { usePet } from './composables/usePet'
import { useTtsQueue } from './composables/useTtsQueue'
import { createReceiptReporter } from './composables/interactionReceipts'

const roles = ref([])
const selectedRole = ref(null)
const roleStatus = ref('加载中...')
const connected = ref(false)
const messageDraft = ref('')
const messages = ref([])
const notices = ref([])
const generationStatus = ref('')
const stage = ref(null)
const generating = ref(false)
const memoryOpen = ref(false)
const memoryRefresh = ref(0)
const pendingText = ref([])
// 当前角色已经送进 TTS 的文本，用于打断后的回声判定与“继续”恢复。
const assistantSpeechText = ref('')
const sessionId = globalThis.crypto?.randomUUID?.() || `session-${Date.now()}`
// 侧边栏拖拽宽度：null 表示未拖拽（走默认 clamp），拖拽后在 320-640px 之间。
const sidebarWidth = ref(null)
const MIN_SIDEBAR = 320
const MAX_SIDEBAR = 640
let messageId = 1
let noticeId = 1
let sendGeneration = 0

const pet = usePet()

function driveCompanions(method, ...args) {
  stage.value?.[method]?.(...args)
  if (pet.petActive.value) pet.petDriver[method]?.(...args)
}

// Phase 1 采用广播策略：主舞台保持动画，桌宠就绪后同步表情、口型和动作。
const companionDriver = computed(() => ({
  setExpression: (...args) => driveCompanions('setExpression', ...args),
  clearExpression: (...args) => driveCompanions('clearExpression', ...args),
  setMouthOpen: (...args) => driveCompanions('setMouthOpen', ...args),
  playAction: (...args) => driveCompanions('playAction', ...args),
  stopAction: (...args) => driveCompanions('stopAction', ...args),
  getExpressions: () => stage.value?.getExpressions?.() || pet.petDriver.getExpressions(),
  getActions: () => stage.value?.getActions?.() || pet.petDriver.getActions(),
}))

const tts = useTtsQueue(companionDriver)
const chat = useChatStream()
let receiptFailureShown = false
const receipts = createReceiptReporter({
  send: async (body) => {
    const response = await fetch(apiUrl('/api/chat/receipts'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      keepalive: true,
      signal: typeof AbortSignal?.timeout === 'function' ? AbortSignal.timeout(2500) : undefined,
    })
    if (!response.ok) throw new Error('交互记录尚未同步')
    receiptFailureShown = false
  },
  onError: () => {
    if (!receiptFailureShown) pushNotice('交互记录尚未同步，将在下一轮重试', { tone: 'error' })
    receiptFailureShown = true
  },
})

function flushPendingText() {
  if (!pendingText.value.length || document.visibilityState !== 'visible') return
  const batch = pendingText.value
  pendingText.value = []
  for (const item of batch) {
    receipts.report(item.role, item.turnId, {
      segment_ids: [item.segmentId],
      channel: 'text',
      state: 'displayed',
    })
  }
}
document.addEventListener('visibilitychange', flushPendingText)
// ASR 完成/错误回调：对话模式激活时路由给对话模式状态机，否则走默认（填入输入框）
let convApi = null
const asr = useAsrStream({
  onComplete: (text) => {
    if (convApi?.active.value) {
      convApi.onAsrComplete(text)
      return
    }
    if (!text) {
      pushNotice('没有识别到语音')
      return
    }
    messageDraft.value = text
  },
  onError: (message) => {
    if (convApi?.active.value) {
      convApi.onAsrError(message)
      return
    }
    pushNotice(`语音识别失败：${message}`, { tone: 'error' })
  },
  onSpeechStart: () => convApi?.onSpeechStart(),
  onSpeechEnd: (event) => convApi?.onSpeechEnd(event),
  onTranscript: (payload) => convApi?.onTranscript(payload),
})

const conv = useConversationMode({
  asr,
  tts,
  sendText: (text) => sendChatText(text),
  cancel: cancelGeneration,
  notify: pushNotice,
  assistantText: () => assistantSpeechText.value,
})
convApi = conv

watch(asr.draft, (text) => {
  // 对话模式下识别文本直接送对话管线，不填输入框
  if (asr.mode.value !== 'idle' && !conv.active.value) messageDraft.value = text
})

function addMessage(role, text) {
  const item = { id: messageId++, role, text }
  messages.value.push(item)
  // Return Vue's proxied entry so streamed text mutations trigger rendering.
  return messages.value[messages.value.length - 1]
}

function pushNotice(text, { tone = 'info', sticky = false, duration = 4200 } = {}) {
  const item = { id: noticeId++, text, tone, sticky }
  notices.value.push(item)
  if (!sticky) window.setTimeout(() => dismissNotice(item.id), duration)
}

function dismissNotice(id) {
  notices.value = notices.value.filter((notice) => notice.id !== id)
}

async function fetchExpressions(sentences) {
  if (!sentences.length) return []
  try {
    const response = await fetch(apiUrl('/api/chat/expressions'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sentences,
        available_expressions: companionDriver.value.getExpressions() || ['happy', 'sad', 'angry', 'surprised', 'relaxed', 'neutral'],
        available_actions: companionDriver.value.getActions() || ['none'],
      }),
    })
    if (!response.ok) throw new Error('表情分析失败')
    return (await response.json()).expressions || []
  } catch {
    return []
  }
}

// 输入框发送：清空草稿后走共用对话管线
async function sendMessage() {
  const text = messageDraft.value.trim()
  if (!text || generating.value || chat.sending.value) return
  messageDraft.value = ''
  await sendChatText(text)
}

// 一轮对话的完整管线（输入框发送与对话模式共用）
async function sendChatText(text) {
  if (!text || generating.value || chat.sending.value) return
  if (!selectedRole.value) {
    pushNotice('请先在左侧选择角色，或通过“新建角色”创建一位陪伴者')
    return
  }

  const activeGeneration = ++sendGeneration
  const turnId = globalThis.crypto?.randomUUID?.() || `turn-${Date.now()}-${Math.random().toString(36).slice(2)}`
  generating.value = true
  messageDraft.value = ''
  const sentences = []
  let assistantMessage = null
  let hasAssistantText = false

  try {
    await tts.ensureAudioReady()
    if (activeGeneration !== sendGeneration) return

    await chat.send({
      roleName: selectedRole.value,
      message: text,
      turnId,
      sessionId,
      onStart: () => {
        tts.stop()
        assistantSpeechText.value = ''
        addMessage('user', text)
        assistantMessage = addMessage('assistant', '思考中...')
        generationStatus.value = '思考中...'
        hasAssistantText = false
      },
      onDelta: (delta) => {
        if (!assistantMessage || !delta) return
        assistantMessage.text = hasAssistantText ? `${assistantMessage.text}${delta}` : delta
        hasAssistantText = true
      },
      onText: (content) => {
        if (assistantMessage) assistantMessage.text = content || '...'
        hasAssistantText = Boolean(content)
      },
      onSentence: async (sentence, metadata = {}) => {
        const index = Number.isInteger(metadata.segment_index) ? metadata.segment_index : sentences.length
        const segmentId = metadata.segment_id || `${turnId}:${index}`
        const expression = guessExpression(sentence)
        sentences.push(sentence)
        assistantSpeechText.value += sentence
        generationStatus.value = `已生成 ${sentences.length} 句...`
        pendingText.value.push({ role: selectedRole.value, turnId, segmentId })
        void nextTick(flushPendingText)
        await tts.enqueue({ text: sentence, roleName: selectedRole.value, ...expression })
      },
      onDone: async (content) => {
        if (assistantMessage) assistantMessage.text = content
        generationStatus.value = sentences.length ? '文本完成，正在匹配表情...' : '完成'
        memoryRefresh.value++
        void nextTick(() => { receipts.flush().catch(() => {}) })
        const expressions = await fetchExpressions(sentences)
        if (activeGeneration !== sendGeneration) return
        for (const item of expressions) {
          const index = item.sentence_index
          if (!Number.isInteger(index) || index < 0 || index >= sentences.length) continue
          tts.patch(index, item)
        }
        generationStatus.value = sentences.length ? `完成，共 ${sentences.length} 句` : '完成'
      },
      onError: (error) => {
        if (assistantMessage) assistantMessage.text = `出错：${error}`
        generationStatus.value = ''
        void receipts.flush().catch(() => {})
      },
    })
  } finally {
    if (activeGeneration === sendGeneration) {
      generating.value = false
      window.setTimeout(() => {
        if (sendGeneration === activeGeneration) generationStatus.value = ''
      }, 2500)
    }
  }
}

function cancelGeneration() {
  sendGeneration += 1
  generating.value = false
  generationStatus.value = ''
  chat.cancel()
  void receipts.flush().catch(() => {})
}

// ============ 侧边栏拖拽分界线 ============
function startSidebarResize(event) {
  event.preventDefault()
  const startX = event.clientX
  const currentWidth = document.querySelector('.sidebar')?.getBoundingClientRect().width ?? 400

  const onMove = (moveEvent) => {
    const width = currentWidth + (moveEvent.clientX - startX)
    sidebarWidth.value = Math.min(Math.max(width, MIN_SIDEBAR), MAX_SIDEBAR)
  }
  const onUp = () => {
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
    document.body.classList.remove('resizing-sidebar')
  }

  document.body.classList.add('resizing-sidebar')
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

async function toggleVoice() {
  if (asr.mode.value === 'idle') {
    tts.stop()
    cancelGeneration()
  }
  await asr.toggle()
}

// ---- 对话模式（无感交流） ----
async function toggleConvMode() {
  if (!navigator.mediaDevices?.getUserMedia) {
    pushNotice('此浏览器不支持语音对话模式，请使用 Chrome 或 Edge 浏览器')
    return
  }
  await conv.toggle()
}

async function openPet() {
  if (!selectedRole.value) {
    pushNotice('请先选择一个角色，再弹出桌宠', { tone: 'error' })
    return
  }
  const opened = await pet.openPet(selectedRole.value)
  pushNotice(
    opened ? '桌宠正在启动，加载完成后会同步表情、口型和动作' : '桌宠启动失败，请查看服务日志',
    { tone: opened ? 'info' : 'error' },
  )
}

async function closePet() {
  const closed = await pet.closePet()
  pushNotice(closed ? '桌宠已收回' : '桌宠收回失败', { tone: closed ? 'info' : 'error' })
}

async function loadRoles() {
  roleStatus.value = '加载中...'
  try {
    const response = await fetch(apiUrl('/api/roles'))
    if (!response.ok) throw new Error('角色加载失败')
    roles.value = (await response.json()).roles || []
    roleStatus.value = ''
    if (roles.value.length && !selectedRole.value) await selectRole(roles.value[0])
  } catch {
    roleStatus.value = '角色加载失败'
  }
}

async function selectRole(roleName) {
  if (selectedRole.value === roleName) return
  cancelGeneration()
  memoryOpen.value = false
  tts.stop()
  selectedRole.value = roleName
  pet.setRole(roleName)
  await loadChatHistory(roleName)
}

async function loadChatHistory(roleName) {
  try {
    const response = await fetch(apiUrl('/api/history'))
    if (!response.ok) throw new Error('历史记录载入失败')
    const history = (await response.json()).history || []
    const roleHistory = history.filter((item) => item.role_name === roleName)
    messages.value = []
    if (!roleHistory.length) return
    pushNotice(`已载入 ${roleHistory.length} 轮历史`)
    for (const turn of roleHistory.slice(-10)) {
      for (const item of turn.messages || []) addMessage(item.role === 'user' ? 'user' : 'assistant', item.content)
    }
  } catch {
    messages.value = []
    pushNotice('历史记录载入失败', { tone: 'error' })
  }
}

async function deleteRole(roleName) {
  const ok = await openConfirm({
    title: '删除角色',
    message: `确定删除角色“${roleName}”吗？`,
    danger: true,
  })
  if (!ok) return
  try {
    const response = await fetch(apiUrl(`/api/roles/${encodeURIComponent(roleName)}`), { method: 'DELETE' })
    if (!response.ok) throw new Error('删除失败')
    if (selectedRole.value === roleName) selectedRole.value = null
    await loadRoles()
    pushNotice(`角色“${roleName}”已删除`)
  } catch {
    pushNotice('删除角色失败', { tone: 'error' })
  }
}

async function uploadVrm(file) {
  pushNotice(`正在加载 VRM：${file.name}...`)
  try {
    const result = await stage.value?.loadFile(file)
    const formData = new FormData()
    formData.append('file', file, file.name)
    const uploadResponse = await fetch(apiUrl('/api/pet/model'), {
      method: 'POST',
      body: formData,
    })
    if (!uploadResponse.ok) {
      const detail = await uploadResponse.json().catch(() => ({}))
      throw new Error(detail.detail || '桌宠模型同步失败')
    }
    const expressions = result?.expressions?.slice(0, 6).join(', ') || '默认表情'
    pushNotice(`模型已切换并同步到桌宠：${file.name} - ${expressions}`)
  } catch (error) {
    pushNotice(`VRM 加载失败：${error.message || '未知错误'}`, { tone: 'error' })
  }
}

function showMemories() {
  if (!selectedRole.value) {
    pushNotice('请先在左侧选择角色')
    return
  }
  memoryOpen.value = true
  void receipts.flush().catch(() => {})
}

async function generateMemory() {
  if (!selectedRole.value) {
    pushNotice('请先在左侧选择角色')
    return
  }
  pushNotice('正在整理角色记忆...')
  try {
    const response = await fetch(apiUrl(`/api/memory/generate?role=${encodeURIComponent(selectedRole.value)}`), { method: 'POST' })
    const data = await response.json()
    pushNotice(data.message || data.status || '记忆整理完成', { sticky: true })
    memoryRefresh.value++
  } catch {
    pushNotice('整理记忆失败', { tone: 'error' })
  }
}

async function clearMemories() {
  if (!selectedRole.value) {
    pushNotice('请先在左侧选择角色')
    return
  }
  const ok = await openConfirm({
    title: '清空记忆',
    message: `确定清空角色“${selectedRole.value}”的长期记忆吗？`,
    danger: true,
  })
  if (!ok) return
  try {
    await fetch(apiUrl(`/api/memory?role=${encodeURIComponent(selectedRole.value)}`), { method: 'DELETE' })
    memoryRefresh.value++
    pushNotice('角色长期记忆已清空')
  } catch {
    pushNotice('清空记忆失败', { tone: 'error' })
  }
}

async function clearHistory() {
  const ok = await openConfirm({
    title: '清空历史',
    message: '确定清空所有对话历史吗？',
    danger: true,
  })
  if (!ok) return
  try {
    await receipts.flush().catch(() => {})
    await fetch(apiUrl('/api/history'), { method: 'DELETE' })
    await fetch(apiUrl('/api/chat/history'), { method: 'DELETE' })
    receipts.discard()
    pendingText.value = []
    messages.value = []
    memoryRefresh.value++
    pushNotice('对话历史已清空')
  } catch {
    pushNotice('清空历史失败', { tone: 'error' })
  }
}

async function checkHealth() {
  try {
    const response = await fetch(apiUrl('/api/health'))
    connected.value = response.ok && (await response.json()).status === 'ok'
  } catch {
    connected.value = false
  }
}

onMounted(async () => {
  pet.startPolling()
  await checkHealth()
  await loadRoles()
})

onBeforeUnmount(async () => {
  cancelGeneration()
  document.removeEventListener('visibilitychange', flushPendingText)
  await receipts.dispose()
  await conv.stop()
  await tts.dispose()
  await asr.dispose()
})
</script>

<template>
  <div class="app-shell">
    <TitleBar />
    <ConfirmDialog />
    <MemoryPanel
      :open="memoryOpen"
      :role-name="selectedRole || ''"
      :refresh-key="memoryRefresh"
      @close="memoryOpen = false"
      @notify="pushNotice"
    />
    <RoleSidebar
      :style="sidebarWidth ? { width: sidebarWidth + 'px' } : null"
      :roles="roles"
      :selected-role="selectedRole"
      :role-status="roleStatus"
      :pet-active="pet.petActive.value"
      @select-role="selectRole"
      @delete-role="deleteRole"
      @upload-vrm="uploadVrm"
      @show-memories="showMemories"
      @generate-memory="generateMemory"
      @clear-memories="clearMemories"
      @clear-history="clearHistory"
      @open-pet="openPet"
      @close-pet="closePet"
    >
      <template #stage>
        <CompanionStage ref="stage" />
      </template>
    </RoleSidebar>

    <div class="sidebar-divider" role="separator" aria-orientation="vertical" @mousedown="startSidebarResize" />

    <ChatPanel
      v-model:message="messageDraft"
      :messages="messages"
      :connected="connected"
      :role-name="selectedRole"
      :can-chat="Boolean(selectedRole)"
      :sending="generating || chat.sending.value"
      :generation-status="generationStatus"
      :notices="notices"
      :voice-mode="asr.mode.value"
      :voice-status="asr.status.value"
      :tts-status="tts.status.value"
      :queue-length="tts.queueLength.value"
      :tts-paused="tts.suspended.value"
      :conv-active="conv.active.value"
      :conv-status="conv.statusText.value"
      :conv-can-resume="conv.canResume.value"
      @send="sendMessage"
      @toggle-voice="toggleVoice"
      @toggle-conv="toggleConvMode"
      @resume-speech="conv.resumeSpeech"
      @dismiss-notice="dismissNotice"
    />
  </div>
</template>
