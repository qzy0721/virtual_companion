<script setup>
import { onBeforeUnmount, onMounted } from 'vue'
import { closeConfirm, useConfirmState } from '../composables/useConfirm'

const state = useConfirmState()

function confirm() {
  closeConfirm(true)
}

function cancel() {
  closeConfirm(false)
}

function onKeydown(event) {
  if (event.key === 'Escape') cancel()
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Teleport to="body">
    <div v-if="state.visible" class="confirm-backdrop" @click.self="cancel">
      <div class="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="confirm-title">
        <h2 id="confirm-title" class="confirm-title">{{ state.title }}</h2>
        <p class="confirm-message">{{ state.message }}</p>
        <div class="confirm-actions">
          <button class="btn btn-secondary" type="button" @click="cancel">取消</button>
          <button
            class="btn"
            :class="state.danger ? 'btn-danger' : 'btn-primary'"
            type="button"
            @click="confirm"
          >
            确定
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
