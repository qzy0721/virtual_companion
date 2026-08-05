<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import {
  available,
  close,
  dragStart,
  getState,
  minimize,
  onWindowState,
  toggleMaximize,
} from '../composables/useWindowBridge'

const maximized = ref(false)
let unsubscribe = () => {}
const iconUrl = `${import.meta.env.BASE_URL}favicon.ico`

onMounted(() => {
  unsubscribe = onWindowState((value) => (maximized.value = value))
  getState()
})

onBeforeUnmount(() => unsubscribe())
</script>

<template>
  <div v-if="available" class="title-bar" @mousedown="dragStart">
    <div class="title-bar-brand">
      <img class="title-bar-icon" :src="iconUrl" alt="" draggable="false" />
      <span class="title-bar-name">虚拟陪伴助手</span>
    </div>
    <div class="title-bar-actions" @mousedown.stop>
      <button class="title-bar-button" type="button" aria-label="最小化" @click="minimize">
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
          <path d="M0.5 5H9.5" stroke="currentColor" stroke-width="1" />
        </svg>
      </button>
      <button
        class="title-bar-button"
        type="button"
        :aria-label="maximized ? '还原' : '最大化'"
        @click="toggleMaximize"
      >
        <svg v-if="!maximized" width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" aria-hidden="true">
          <rect x="0.5" y="0.5" width="9" height="9" rx="1" />
        </svg>
        <svg v-else width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" aria-hidden="true">
          <path d="M2.5 2.5V0.5H9.5V7.5H7.5" />
          <path d="M0.5 2.5V9.5H7.5V2.5Z" />
        </svg>
      </button>
      <button class="title-bar-button title-bar-close" type="button" aria-label="关闭" @click="close">
        <svg width="10" height="10" viewBox="0 0 10 10" stroke="currentColor" aria-hidden="true">
          <path d="M0.5 0.5L9.5 9.5M9.5 0.5L0.5 9.5" stroke-width="1" />
        </svg>
      </button>
    </div>
  </div>
</template>
