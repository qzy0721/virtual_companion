// 全局确认弹窗单例：openConfirm 返回 Promise，确认/取消由 ConfirmDialog 组件回填。
// 替代 window.confirm（原生弹窗标题显示 URL，观感生硬）。

import { reactive } from 'vue'

const state = reactive({
  visible: false,
  title: '提示',
  message: '',
  danger: false,
  resolve: null,
})

export function openConfirm({ title = '提示', message, danger = false } = {}) {
  state.title = title
  state.message = message
  state.danger = danger
  state.visible = true
  return new Promise((resolve) => {
    state.resolve = resolve
  })
}

export function useConfirmState() {
  return state
}

export function closeConfirm(result) {
  state.visible = false
  state.resolve?.(result)
  state.resolve = null
}
