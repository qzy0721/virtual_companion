<script setup>
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { apiUrl } from '../api'

const props = defineProps({
  open: { type: Boolean, default: false },
  roleName: { type: String, default: '' },
  refreshKey: { type: Number, default: 0 },
})
// notify(message, options) can be connected directly to App's pushNotice.
// changed({ roleName, id, action }) is emitted after a successful write.
const emit = defineEmits(['close', 'changed', 'notify', 'state'])
const titleId = useId()
const dialog = ref(null)
const active = ref(false)
const loading = ref(false)
const loaded = ref(false)
const loadError = ref('')
const memories = ref([])
const legacy = ref([])
const total = ref(0)
const processing = ref(false)
const processingError = ref(false)
const unresolved = ref(false)
const editing = ref(null)
const draft = ref('')
const confirming = ref(null)
const busy = ref(false)
const actionError = ref('')
const feedback = ref('')
const retryError = ref('')
const autoRefreshes = ref(0)
let refreshTimer
let refreshPending = false
const MAX_AUTO_REFRESHES = 4
let session = 0
let loadSequence = 0
let loadController
let writeController

const role = computed(() => (props.roleName || '').trim())
const validId = (id) => (typeof id === 'number' && Number.isFinite(id)) ||
  (typeof id === 'string' && Boolean(id.trim()) && !['.', '..'].includes(id))
const deleted = (memory) => ['revoked', 'retracted', 'withdrawn', 'deleted'].includes(memory.status)
const editable = (memory) => validId(memory.id) && !memory.legacy &&
  !['revoked', 'retracted', 'withdrawn', 'deleted', 'superseded'].includes(memory.status)

function words(value) {
  return typeof value === 'string' ? value : ''
}

function normalizeMemory(value, index, isLegacy = false) {
  const item = typeof value === 'string' ? { content: value } : (value || {})
  const rawSources = item.sources ?? item.source_quotes ?? []
  const sources = (Array.isArray(rawSources) ? rawSources : [rawSources]).flatMap((source) => {
    const quote = typeof source === 'string' ? source : words(source?.quote ?? source?.source_quote ?? source?.text ?? source?.content)
    if (!quote) return []
    const speaker = source?.speaker ?? source?.role
    return [{ quote, speaker: speaker === 'user' ? '你说过' : speaker === 'assistant' ? '角色说过' : '对话原话' }]
  })
  return {
    id: item.id,
    key: `${isLegacy ? 'legacy' : 'memory'}-${item.id ?? index}`,
    content: words(item.content ?? item.summary ?? item.text),
    kind: words(item.kind).toLowerCase(),
    status: words(item.status).toLowerCase(),
    revision: item.revision,
    sources,
    legacy: isLegacy || item.kind === 'legacy_summary',
  }
}

function kindLabel(memory) {
  if (memory.legacy) return '旧摘要'
  if (['fact', 'user_fact', 'preference'].includes(memory.kind)) return '事实'
  if (['suggestion', 'proposal', 'advice', 'assistant_suggestion'].includes(memory.kind)) return '建议'
  if (['commitment', 'agreement'].includes(memory.kind)) return memory.status === 'accepted' ? '已接受的约定' : '待确认的提议'
  return '记忆记录'
}

function statusLabel(memory) {
  if (memory.legacy) return '未核验'
  if (deleted(memory)) return '已删除'
  if (memory.status === 'superseded') return '已替代'
  if (memory.status === 'rejected') return '已拒绝'
  if (memory.status === 'user_edited') return '已修改'
  if (['accepted', 'confirmed'].includes(memory.status)) return '已接受'
  if (['pending', 'proposed', 'unresolved', 'pending_confirmation', 'disputed'].includes(memory.status)) return '待确认'
  if (['active', 'asserted'].includes(memory.status) && ['fact', 'user_fact', 'preference'].includes(memory.kind)) return '已记录'
  if (['suggestion', 'proposal', 'advice', 'assistant_suggestion', 'commitment', 'agreement'].includes(memory.kind)) return '待确认'
  return '未核验'
}

function invalidate() {
  window.clearTimeout(refreshTimer)
  refreshPending = false
  session++
  loadSequence++
  loadController?.abort()
  writeController?.abort()
}

function resetEditor() {
  editing.value = null
  confirming.value = null
  draft.value = ''
  actionError.value = ''
}

function close() {
  invalidate()
  active.value = false
  busy.value = false
  resetEditor()
  dialog.value?.close()
  emit('close')
}

function scheduleRefresh() {
  window.clearTimeout(refreshTimer)
  if (!active.value || !processing.value || loadError.value || autoRefreshes.value >= MAX_AUTO_REFRESHES) return
  refreshTimer = window.setTimeout(() => {
    if (document.visibilityState !== 'visible' || editing.value || confirming.value || busy.value) return
    autoRefreshes.value++
    void load()
  }, 2000)
}

async function load() {
  if (!active.value || !role.value) return
  if (busy.value || editing.value || confirming.value) {
    refreshPending = true
    return
  }
  refreshPending = false
  window.clearTimeout(refreshTimer)
  loadController?.abort()
  const controller = new AbortController()
  loadController = controller
  const currentSession = session
  const currentLoad = ++loadSequence
  const currentRole = role.value
  const current = () => active.value && session === currentSession && loadSequence === currentLoad && role.value === currentRole
  loading.value = true
  loadError.value = ''
  try {
    const response = await fetch(apiUrl(`/api/memory?role=${encodeURIComponent(currentRole)}`), { signal: controller.signal })
    if (!response.ok) throw new Error('load')
    const data = await response.json()
    if (!Array.isArray(data.memories)) throw new Error('format')
    if (!current()) return
    const normalized = data.memories.map((item, index) => normalizeMemory(item, index))
    memories.value = normalized.filter(item => !item.legacy && !deleted(item))
    const old = data.legacy_memories ?? []
    legacy.value = [...normalized.filter(item => item.legacy), ...(Array.isArray(old) ? old : [old]).map((item, index) => normalizeMemory(item, index, true))]
    total.value = memories.value.length
    processing.value = Boolean(data.processing)
    processingError.value = Array.isArray(data.errors) ? data.errors.length > 0 : Boolean(data.errors)
    unresolved.value = Boolean(data.unresolved)
    loaded.value = true
    emit('state', { roleName: currentRole, total: total.value, processing: processing.value, errors: processingError.value, unresolved: unresolved.value })
  } catch (error) {
    if (current() && error.name !== 'AbortError') {
      loadError.value = '读取失败，请重试'
      emit('state', { roleName: currentRole, failed: true })
    }
  } finally {
    if (current()) {
      loading.value = false
      scheduleRefresh()
    }
  }
}

function refresh() {
  autoRefreshes.value = 0
  void load()
}

watch(() => props.refreshKey, refresh)
watch([editing, confirming, busy], () => {
  if (editing.value || confirming.value || busy.value) return
  if (refreshPending) void load()
  else scheduleRefresh()
})

async function retryProcessing() {
  if (!active.value || !role.value || busy.value || loading.value || editing.value || confirming.value) return
  const currentSession = session
  const currentRole = role.value
  const current = () => active.value && session === currentSession && role.value === currentRole
  writeController = new AbortController()
  window.clearTimeout(refreshTimer)
  busy.value = true
  retryError.value = ''
  feedback.value = ''
  try {
    const response = await fetch(apiUrl(`/api/memory/generate?role=${encodeURIComponent(currentRole)}`), { method: 'POST', signal: writeController.signal })
    if (!response.ok) throw new Error('retry')
    const data = await response.json()
    if (!current()) return
    feedback.value = data.status === 'failed' ? '整理失败，请重试' : '已检查，请查看记录'
  } catch (error) {
    if (current() && error.name !== 'AbortError') retryError.value = '结果未确认，请刷新'
  } finally {
    if (current()) {
      busy.value = false
      refresh()
    }
  }
}

watch([() => props.open, () => props.roleName], ([open]) => {
  invalidate()
  active.value = open
  loading.value = false
  loaded.value = false
  loadError.value = ''
  memories.value = []
  legacy.value = []
  total.value = 0
  busy.value = false
  processing.value = false
  processingError.value = false
  unresolved.value = false
  feedback.value = ''
  retryError.value = ''
  autoRefreshes.value = 0
  resetEditor()
  if (!open) {
    dialog.value?.close()
    return
  }
  const currentSession = session
  nextTick(() => {
    if (active.value && currentSession === session && !dialog.value?.open) dialog.value?.showModal()
  })
  load()
}, { immediate: true, flush: 'sync' })

function edit(memory) {
  if (busy.value || loading.value || !editable(memory)) return
  resetEditor()
  feedback.value = ''
  editing.value = memory
  draft.value = memory.content
  nextTick(() => dialog.value?.querySelector('textarea')?.focus())
}

function askRevoke(memory) {
  if (busy.value || loading.value || !editable(memory)) return
  resetEditor()
  feedback.value = ''
  confirming.value = memory
  nextTick(() => dialog.value?.querySelector('[data-cancel-revoke]')?.focus())
}

async function mutate(action) {
  const memory = action === 'edit' ? editing.value : confirming.value
  if (!active.value || !memory || busy.value || loading.value || !editable(memory)) return
  const content = draft.value.trim()
  if (action === 'edit' && !content) {
    actionError.value = '请输入记忆内容'
    return
  }
  const currentSession = session
  const currentRole = role.value
  const current = () => active.value && session === currentSession && role.value === currentRole
  writeController = new AbortController()
  busy.value = true
  actionError.value = ''
  feedback.value = ''
  try {
    const path = `/api/memory/${encodeURIComponent(memory.id)}`
    const response = await fetch(apiUrl(action === 'edit' ? path : `${path}?role=${encodeURIComponent(currentRole)}`), {
      method: action === 'edit' ? 'PATCH' : 'DELETE',
      signal: writeController.signal,
      ...(action === 'edit' ? {
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role_name: currentRole, content, revision: memory.revision }),
      } : {}),
    })
    if (!current()) return
    if (!response.ok) {
      if (response.status === 409 || response.status === 412) {
        actionError.value = '记忆已更新，请取消后刷新'
      } else if (response.status === 404) {
        actionError.value = '记忆已不可用，请取消后刷新'
      } else {
        actionError.value = action === 'edit' ? '保存失败，请重试' : '删除失败，请重试'
      }
      return
    }
    // Only remove after the server accepts deletion; keep source audits on the server.
    if (action !== 'edit') {
      memories.value = memories.value.filter(item => item.key !== memory.key)
      total.value = memories.value.length
      emit('state', { roleName: currentRole, total: total.value, processing: processing.value, errors: processingError.value, unresolved: unresolved.value })
    }
    resetEditor()
    feedback.value = action === 'edit' ? '已保存' : '已删除'
    emit('changed', { roleName: currentRole, id: memory.id, action: action === 'edit' ? 'updated' : 'revoked' })
    emit('notify', feedback.value)
    // Refresh authoritative status/revision; a PATCH need not return a particular envelope.
    if (!current()) return
    busy.value = false
    await load()
  } catch (error) {
    if (current() && error.name !== 'AbortError') actionError.value = '结果未确认，请取消后刷新'
  } finally {
    if (current()) busy.value = false
  }
}

function cancelDialog(event) {
  event.preventDefault()
  if ((editing.value || confirming.value) && !busy.value) resetEditor()
  else close()
}

onBeforeUnmount(() => {
  invalidate()
  dialog.value?.close()
})
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" class="memory-panel" :aria-labelledby="titleId" @cancel="cancelDialog">
      <div class="memory-shell">
        <header class="memory-header">
          <div>
            <h2 :id="titleId">{{ roleName || '当前角色' }}的记忆</h2>
          </div>
          <button class="btn btn-secondary btn-small" type="button" aria-label="关闭记忆面板" @click="close">关闭</button>
        </header>

        <div class="memory-body" :aria-busy="loading || busy">
          <p v-if="!role" class="memory-empty">请先选择角色</p>
          <template v-else>
            <div class="memory-toolbar">
              <span>{{ loaded ? `${total} 条记忆` : '记忆' }}</span>
              <button class="btn btn-secondary btn-small" type="button" :disabled="loading || busy || !!editing || !!confirming" @click="refresh">{{ loading ? '加载中…' : '刷新' }}</button>
            </div>
            <p v-if="loading" role="status">读取中…</p>
            <div v-if="loadError" class="memory-error" role="alert">
              <p>{{ loadError }}</p>
              <button class="btn btn-secondary btn-small" type="button" :disabled="loading || busy || !!editing || !!confirming" @click="refresh">重试</button>
            </div>
            <p v-if="feedback" class="memory-feedback" role="status">{{ feedback }}</p>
            <p v-if="processing" class="memory-hint" role="status">{{ autoRefreshes >= MAX_AUTO_REFRESHES ? '仍在整理，请稍后刷新' : '整理中…' }}</p>
            <div v-if="processingError || processing" class="memory-retry">
              <p v-if="processingError" class="memory-error" role="status">部分对话整理失败</p>
              <button class="btn btn-secondary btn-small" type="button" :disabled="loading || busy || !!editing || !!confirming" @click="retryProcessing">{{ busy && !editing && !confirming ? '整理中…' : '重试整理' }}</button>
            </div>
            <p v-if="retryError" class="memory-error" role="alert">{{ retryError }}</p>
            <p v-if="unresolved" class="memory-hint" role="status">有待确认的建议</p>
            <p v-if="loaded && !memories.length && !loading" class="memory-empty">暂无记忆</p>

            <ul v-if="memories.length" class="memory-list" aria-label="角色记忆">
              <li v-for="memory in memories" :key="memory.key" class="memory-card" :class="{ 'memory-revoked': !editable(memory) }">
                <div class="memory-labels"><span>{{ kindLabel(memory) }}</span><span class="memory-badge">{{ statusLabel(memory) }}</span></div>
                <template v-if="editing?.key === memory.key">
                  <form @submit.prevent="mutate('edit')">
                    <label class="memory-editor">记忆内容
                      <textarea v-model="draft" rows="4" :disabled="busy" required />
                    </label>
                    <p v-if="actionError" class="memory-error" role="alert">{{ actionError }}</p>
                    <div class="memory-actions">
                      <button class="btn btn-primary btn-small" type="submit" :disabled="busy || !draft.trim() || draft.trim() === memory.content">{{ busy ? '保存中…' : '保存' }}</button>
                      <button class="btn btn-secondary btn-small" type="button" :disabled="busy" @click="resetEditor">取消</button>
                    </div>
                  </form>
                </template>
                <p v-else class="memory-content">{{ memory.content || '暂无内容' }}</p>

                <details v-if="memory.sources.length" class="memory-sources">
                  <summary>来源（{{ memory.sources.length }}）</summary>
                  <blockquote v-for="(source, index) in memory.sources" :key="index"><small>{{ source.speaker }}</small><p>{{ source.quote }}</p></blockquote>
                </details>
                <p v-else class="memory-hint">暂无来源</p>

                <div v-if="confirming?.key === memory.key" class="memory-confirm" role="group" aria-label="删除记忆">
                  <p>删除这条记忆？</p>
                  <p v-if="actionError" class="memory-error" role="alert">{{ actionError }}</p>
                  <div class="memory-actions">
                    <button data-cancel-revoke class="btn btn-secondary btn-small" type="button" :disabled="busy" @click="resetEditor">取消</button>
                    <button class="btn btn-secondary btn-small btn-warn memory-danger" type="button" :disabled="busy" @click="mutate('revoke')">{{ busy ? '删除中…' : '删除' }}</button>
                  </div>
                </div>
                <div v-else-if="editable(memory) && editing?.key !== memory.key" class="memory-actions">
                  <button class="btn btn-secondary btn-small" type="button" :disabled="busy || loading || !!editing || !!confirming" @click="edit(memory)">修改</button>
                  <button class="btn btn-secondary btn-small btn-warn" type="button" :disabled="busy || loading || !!editing || !!confirming" @click="askRevoke(memory)">删除</button>
                </div>
              </li>
            </ul>

            <section v-if="legacy.length" class="memory-legacy" aria-label="未核验的旧摘要">
              <h3>旧摘要（未核验）</h3>
              <details v-for="item in legacy" :key="item.key"><summary>查看旧摘要</summary><p class="memory-content">{{ item.content }}</p></details>
            </section>
          </template>
        </div>
      </div>
    </dialog>
  </Teleport>
</template>

<style scoped>
.memory-panel { width: min(700px, calc(100vw - 32px)); max-height: calc(100dvh - 48px); margin: auto; padding: 0; overflow: hidden; border: 1px solid var(--border); border-radius: 16px; color: var(--text-primary); background: var(--bg-panel); box-shadow: 0 18px 48px var(--shadow-dialog); font: inherit; }
.memory-panel::backdrop { background: var(--bg-overlay); }
.memory-shell { display: flex; flex-direction: column; max-height: calc(100dvh - 50px); }
.memory-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 20px 24px 16px; border-bottom: 1px solid var(--border); }
.memory-header h2 { margin: 0; font-size: 1.05rem; overflow-wrap: anywhere; }
.memory-hint { margin: 8px 0; color: var(--text-muted); font-size: 0.78rem; line-height: 1.6; }
.memory-header > button { flex-shrink: 0; }
.memory-body { min-height: 0; padding: 16px 24px 24px; overflow-y: auto; overscroll-behavior: contain; font-size: 0.85rem; line-height: 1.6; }
.memory-toolbar, .memory-labels, .memory-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.memory-toolbar { justify-content: space-between; color: var(--text-secondary); }
.memory-list { display: grid; gap: 12px; margin: 16px 0 0; padding: 0; list-style: none; }
.memory-card { min-width: 0; padding: 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--bg-muted); }
.memory-labels { justify-content: space-between; color: var(--text-secondary); font-size: 0.75rem; font-weight: 600; }
.memory-badge { padding: 2px 8px; border: 1px solid var(--accent-border-soft); border-radius: 999px; color: var(--accent-deep); background: var(--bg-note); }
.memory-content, .memory-sources blockquote p { margin: 12px 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.memory-revoked .memory-content { color: var(--text-muted); }
.memory-revoked .memory-badge { color: var(--text-muted); border-color: var(--border); background: var(--bg-subtle); }
.memory-sources { margin: 12px 0; }
summary { color: var(--accent-deep); cursor: pointer; font-size: 0.78rem; }
.memory-sources blockquote { margin: 12px 0 0; padding: 4px 12px; border-left: 3px solid var(--accent-border); color: var(--text-secondary); background: var(--bg-panel); }
.memory-sources small { color: var(--text-muted); }
.memory-editor { display: grid; gap: 8px; margin: 12px 0; color: var(--text-secondary); }
.memory-editor textarea { box-sizing: border-box; width: 100%; min-height: 100px; padding: 10px 12px; resize: vertical; border: 1px solid var(--border-strong); border-radius: 8px; color: var(--text-primary); background: var(--bg-panel); font: inherit; }
.memory-panel :focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.memory-actions { margin-top: 12px; }
.memory-confirm { margin-top: 12px; padding: 12px; border: 1px solid var(--accent-border); border-radius: 8px; background: var(--bg-panel); }
.memory-confirm p { margin: 0 0 8px; }
.memory-danger { color: var(--text-danger); background: var(--bg-danger); }
.memory-danger:hover { background: var(--bg-danger-hover); }
.memory-error { margin: 12px 0; color: var(--text-error); overflow-wrap: anywhere; }
.memory-feedback { color: var(--accent-deep); }
.memory-empty { padding: 24px 0; color: var(--text-muted); text-align: center; }
.memory-legacy { margin-top: 24px; padding-top: 12px; border-top: 1px solid var(--border); }
.memory-legacy h3 { margin: 0; color: var(--text-secondary); font-size: 0.88rem; }
.memory-legacy details { margin-top: 12px; }
.memory-panel button:disabled { opacity: 0.5; cursor: not-allowed; }
@media (max-width: 480px) {
  .memory-header { padding: 16px; }
  .memory-body { padding: 12px 16px 20px; }
  .memory-card { padding: 12px; }
}
</style>
