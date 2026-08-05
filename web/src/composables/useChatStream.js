import { ref } from 'vue'
import { apiUrl } from '../api'

const MAX_DELTA_CHARS_PER_FRAME = 12

function renderOnNextFrame(callback, value) {
  if (typeof window === 'undefined' || typeof window.requestAnimationFrame !== 'function') {
    callback?.(value)
    return Promise.resolve()
  }

  return new Promise((resolve) => {
    const characters = Array.from(value || '')
    let offset = 0

    function render() {
      callback?.(characters.slice(offset, offset + MAX_DELTA_CHARS_PER_FRAME).join(''))
      offset += MAX_DELTA_CHARS_PER_FRAME
      if (offset < characters.length) window.requestAnimationFrame(render)
      else resolve()
    }

    window.requestAnimationFrame(render)
  })
}

export function useChatStream() {
  const sending = ref(false)
  let controller = null
  let requestId = 0

  function cancel() {
    requestId += 1
    controller?.abort()
    controller = null
    sending.value = false
  }

  async function send({ roleName, message, onStart, onDelta, onText, onSentence, onDone, onError }) {
    if (!roleName || !message || sending.value) return false

    const id = ++requestId
    controller = new AbortController()
    sending.value = true
    onStart?.()

    try {
      const response = await fetch(apiUrl('/api/chat'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
        body: JSON.stringify({ role_name: roleName, message }),
        signal: controller.signal,
      })
      if (!response.ok || !response.body) throw new Error('对话服务暂不可用')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let eventType = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done || id !== requestId) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line) {
            eventType = ''
            continue
          }
          if (line.startsWith('event: ')) {
            eventType = line.slice(7).trim()
            continue
          }
          if (!line.startsWith('data: ')) continue
          try {
            const payload = JSON.parse(line.slice(6))
            if (eventType === 'delta') await renderOnNextFrame(onDelta, payload.data)
            if (eventType === 'text') await onText?.(payload.data)
            if (eventType === 'sentence') await onSentence?.(payload.data)
            if (eventType === 'done') await onDone?.(payload.data)
            if (eventType === 'error') await onError?.(payload.data)
          } catch {
            // Ignore malformed individual SSE records and keep the stream alive.
          }
        }
      }
      return id === requestId
    } catch (error) {
      if (error.name !== 'AbortError' && id === requestId) onError?.(error.message)
      return false
    } finally {
      if (id === requestId) {
        controller = null
        sending.value = false
      }
    }
  }

  return { sending, send, cancel }
}
