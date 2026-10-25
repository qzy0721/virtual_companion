<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'

const message = defineModel('message', { default: '' })
const props = defineProps({
  messages: { type: Array, default: () => [] },
  connected: { type: Boolean, default: false },
  roleName: { type: String, default: '' },
  canChat: { type: Boolean, default: false },
  sending: { type: Boolean, default: false },
  generationStatus: { type: String, default: '' },
  notices: { type: Array, default: () => [] },
  voiceMode: { type: String, default: 'idle' },
  voiceStatus: { type: String, default: '' },
  ttsStatus: { type: String, default: '就绪' },
  queueLength: { type: Number, default: 0 },
  ttsPaused: { type: Boolean, default: false },
  convActive: { type: Boolean, default: false },
  convStatus: { type: String, default: '' },
  convCanResume: { type: Boolean, default: false },
})

const emit = defineEmits(['send', 'toggle-voice', 'toggle-conv', 'resume-speech', 'dismiss-notice'])
const messagesElement = ref(null)
const composerElement = ref(null)
let scrollFrame = 0
let resizeFrame = 0
let stickToBottom = true

function updateStickToBottom() {
  const element = messagesElement.value
  if (!element) return
  stickToBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 48
}

function queueScrollToLatest() {
  if (!stickToBottom || scrollFrame) return
  scrollFrame = window.requestAnimationFrame(() => {
    scrollFrame = 0
    const element = messagesElement.value
    if (element && stickToBottom) element.scrollTop = element.scrollHeight
  })
}

watch(() => props.messages, queueScrollToLatest, { deep: true, flush: 'post' })

function queueComposerResize() {
  if (resizeFrame) return
  resizeFrame = window.requestAnimationFrame(() => {
    resizeFrame = 0
    const element = composerElement.value
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${Math.min(element.scrollHeight, 132)}px`
    element.style.overflowY = element.scrollHeight > 132 ? 'auto' : 'hidden'
  })
}

watch(message, queueComposerResize, { flush: 'post' })

onBeforeUnmount(() => {
  if (scrollFrame) window.cancelAnimationFrame(scrollFrame)
  if (resizeFrame) window.cancelAnimationFrame(resizeFrame)
})

function submit() {
  if (message.value.trim() && !props.sending && props.voiceMode === 'idle' && !props.convActive) emit('send')
}

function handleComposerKeydown(event) {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return
  event.preventDefault()
  submit()
}
</script>

<template>
  <main class="chat-panel">
    <header class="chat-header">
      <h1><span class="connection-dot" :class="{ offline: !connected }"></span>{{ roleName || '虚拟陪伴助手' }}</h1>
      <span class="muted-text">{{ connected ? '已连接' : '离线' }}</span>
    </header>

    <div class="notice-stack" aria-live="polite">
      <div v-for="notice in notices" :key="notice.id" class="notice-toast" :class="notice.tone">
        <span class="notice-text">{{ notice.text }}</span>
        <button v-if="notice.sticky" class="notice-close" type="button" aria-label="关闭" @click="emit('dismiss-notice', notice.id)">&times;</button>
      </div>
    </div>

    <section ref="messagesElement" class="messages" aria-live="polite" @scroll.passive="updateStickToBottom">
      <div v-if="!messages.length" class="chat-empty">
        <strong>{{ roleName ? `开始和 ${roleName} 对话吧` : '选择角色开始对话' }}</strong>
        <span>{{ roleName ? '输入消息或点击语音按钮，开启一次交流。' : '在左侧选择一位陪伴者，或通过“新建角色”创建一位。' }}</span>
      </div>
      <div v-for="item in messages" :key="item.id" class="message" :class="item.role">{{ item.text }}</div>
    </section>

    <div class="status-bar">
      <span v-if="generationStatus">{{ generationStatus }}</span>
      <span :class="{ 'tts-paused': ttsPaused }">队列: {{ queueLength }}{{ ttsStatus !== '就绪' ? ` · ${ttsStatus}` : '' }}</span>
      <span>{{ voiceStatus }}</span>
      <span v-if="convStatus" class="conv-status">{{ convStatus }}</span>
      <button
        v-if="convCanResume"
        class="btn btn-secondary btn-small status-resume"
        type="button"
        @click="emit('resume-speech')"
      >继续播放</button>
    </div>

    <form class="input-bar" @submit.prevent="submit">
      <textarea
        ref="composerElement"
        v-model="message"
        rows="1"
        :readonly="voiceMode !== 'idle'"
        :aria-busy="sending || voiceMode !== 'idle'"
        :placeholder="voiceMode !== 'idle' ? '正在识别...' : sending ? '正在生成回复...' : canChat ? '输入消息...' : '可先输入消息，选择角色后发送'"
        @keydown="handleComposerKeydown"
      ></textarea>
      <button class="btn btn-primary" :disabled="sending || voiceMode !== 'idle' || convActive || !message.trim()" type="submit">发送</button>
      <button
        class="btn btn-secondary voice-button"
        :class="{ recording: voiceMode === 'recording' || voiceMode === 'stopping', connecting: voiceMode === 'connecting' }"
        :disabled="!canChat || sending || convActive"
        :aria-pressed="voiceMode !== 'idle'"
        type="button"
        @click="emit('toggle-voice')"
      >{{ voiceMode === 'connecting' ? '连接中' : voiceMode === 'idle' ? '语音' : '停止' }}</button>
      <button
        class="btn btn-secondary conv-mode-btn"
        :class="{ active: convActive }"
        :disabled="!canChat || (!convActive && (sending || voiceMode !== 'idle'))"
        :aria-pressed="convActive"
        title="常驻麦克风自动聆听；说话可随时打断，说“继续”接着听"
        type="button"
        @click="emit('toggle-conv')"
      >{{ convActive ? ' 结束对话' : ' 对话模式' }}</button>
    </form>
  </main>
</template>
