<script setup>
import { computed, onMounted, ref } from 'vue'
import { apiUrl } from './api'
import ConfirmDialog from './components/ConfirmDialog.vue'
import TitleBar from './components/TitleBar.vue'
import { openConfirm } from './composables/useConfirm'

const roles = ref([])
const selectedRole = ref('')
const roleDetail = ref(null)
const roleSummaries = ref({})
const detailDrawerOpen = ref(false)
const listStatus = ref('正在载入角色...')
const detailStatus = ref('')
const actionStatus = ref('')
const creating = ref(false)
const deleting = ref(false)
const voiceCapabilitiesLoading = ref(true)
const voiceCapabilities = ref({
  dashscopeConfigured: false,
  ossConfigured: false,
  defaultProvider: 'cosyvoice',
})

const roleName = ref('')
const createMode = ref('manual')
const systemPrompt = ref('')
const baikeQuery = ref('')
const voiceProvider = ref('cosyvoice')
const referenceAudio = ref(null)
const audioLabel = computed(() => referenceAudio.value?.name || '未选择参考音频，将使用默认音频')
const qwenAvailable = computed(() => voiceCapabilities.value.dashscopeConfigured)
const cosyVoiceAvailable = computed(() => (
  voiceCapabilities.value.dashscopeConfigured && voiceCapabilities.value.ossConfigured
))
const selectedProviderAvailable = computed(() => (
  voiceProvider.value === 'cosyvoice' ? cosyVoiceAvailable.value : qwenAvailable.value
))
const createFormReady = computed(() => (
  roleName.value.trim()
  && (createMode.value !== 'manual' || systemPrompt.value.trim())
  && selectedProviderAvailable.value
  && !voiceCapabilitiesLoading.value
))
const createButtonLabel = computed(() => {
  if (creating.value) return '创建中...'
  if (voiceCapabilitiesLoading.value) return '正在检查语音配置'
  if (!selectedProviderAvailable.value) return '请先配置语音服务'
  if (!roleName.value.trim()) return '请填写角色名称'
  if (createMode.value === 'manual' && !systemPrompt.value.trim()) return '请填写角色提示词'
  return '创建角色'
})
const providerHint = computed(() => {
  if (voiceCapabilitiesLoading.value) return '正在检查语音服务配置...'
  if (!voiceCapabilities.value.dashscopeConfigured) return '尚未配置 DashScope API Key，语音引擎暂不可用。'
  if (voiceProvider.value === 'cosyvoice' && !voiceCapabilities.value.ossConfigured) return 'CosyVoice 还需要 OSS Access Key、Secret、Endpoint 与 Bucket。'
  return voiceProvider.value === 'cosyvoice'
    ? '配置完整，参考音频会先上传 OSS，再创建 CosyVoice 音色。'
    : '配置完整，参考音频将直接提交给 Qwen3-TTS，无需 OSS。'
})

async function readError(response, fallback) {
  const data = await response.json().catch(() => ({}))
  return data.detail || data.message || fallback
}

async function fetchRoleDetail(roleNameToLoad) {
  try {
    const response = await fetch(apiUrl(`/api/roles/${encodeURIComponent(roleNameToLoad)}`))
    if (!response.ok) throw new Error(await readError(response, '角色资料载入失败'))
    return await response.json()
  } catch {
    return null
  }
}

async function loadVoiceCapabilities() {
  voiceCapabilitiesLoading.value = true
  try {
    const response = await fetch(apiUrl('/api/settings/status'), {
      cache: 'no-store',
      headers: { 'Cache-Control': 'no-store' },
    })
    if (!response.ok) throw new Error(await readError(response, '语音配置状态载入失败'))
    const data = await response.json()
    const capabilities = {
      dashscopeConfigured: Boolean(data.dashscope_api_key?.configured),
      ossConfigured: Boolean(
        data.oss_access_key_id?.configured
        && data.oss_access_key_secret?.configured
        && data.oss_endpoint
        && data.oss_bucket_name
      ),
      defaultProvider: data.default_tts_provider || 'cosyvoice',
    }
    voiceCapabilities.value = capabilities

    const defaultAvailable = capabilities.defaultProvider === 'cosyvoice'
      ? capabilities.dashscopeConfigured && capabilities.ossConfigured
      : capabilities.dashscopeConfigured
    if (defaultAvailable) voiceProvider.value = capabilities.defaultProvider
    else if (capabilities.dashscopeConfigured) voiceProvider.value = 'qwen_tts'
  } catch (error) {
    actionStatus.value = error.message || '语音配置状态载入失败'
  } finally {
    voiceCapabilitiesLoading.value = false
  }
}

async function loadRoleDetail(roleNameToLoad) {
  if (!roleNameToLoad) {
    roleDetail.value = null
    return
  }

  detailStatus.value = '正在载入角色资料...'
  roleDetail.value = roleSummaries.value[roleNameToLoad] || null
  const detail = await fetchRoleDetail(roleNameToLoad)
  if (detail) {
    roleSummaries.value = { ...roleSummaries.value, [roleNameToLoad]: detail }
    roleDetail.value = detail
    detailStatus.value = ''
  } else if (!roleDetail.value) {
    detailStatus.value = '角色资料载入失败'
  }
}

async function loadRoles(preferredRole = selectedRole.value) {
  listStatus.value = '正在载入角色...'
  try {
    const response = await fetch(apiUrl('/api/roles'))
    if (!response.ok) throw new Error(await readError(response, '角色列表载入失败'))
    roles.value = (await response.json()).roles || []
    const detailEntries = await Promise.all(roles.value.map(async (name) => [name, await fetchRoleDetail(name)]))
    roleSummaries.value = Object.fromEntries(detailEntries.filter(([, detail]) => detail))
    listStatus.value = ''
    const nextRole = roles.value.includes(preferredRole) ? preferredRole : roles.value[0] || ''
    selectedRole.value = nextRole
    roleDetail.value = roleSummaries.value[nextRole] || null
  } catch (error) {
    listStatus.value = error.message || '角色列表载入失败'
  }
}

async function selectRole(name) {
  selectedRole.value = name
  roleDetail.value = roleSummaries.value[name] || null
  if (!roleDetail.value) await loadRoleDetail(name)
}

async function openRoleDetail(name) {
  await selectRole(name)
  detailDrawerOpen.value = true
}

function selectAudio(event) {
  referenceAudio.value = event.target.files?.[0] ?? null
}

function resetCreateForm() {
  roleName.value = ''
  systemPrompt.value = ''
  baikeQuery.value = ''
  referenceAudio.value = null
}

async function createRole() {
  const name = roleName.value.trim()
  if (!name) {
    actionStatus.value = '请先填写角色名称'
    return
  }
  if (createMode.value === 'manual' && !systemPrompt.value.trim()) {
    actionStatus.value = '手写设定模式需要填写角色提示词'
    return
  }
  if (!selectedProviderAvailable.value) {
    actionStatus.value = providerHint.value
    return
  }

  const form = new FormData()
  form.append('role_name', name)
  form.append('voice_provider', voiceProvider.value)
  if (referenceAudio.value) form.append('audio', referenceAudio.value)

  let path = '/api/roles'
  if (createMode.value === 'manual') {
    path = '/api/roles/register'
    form.append('system_prompt', systemPrompt.value.trim())
  } else if (createMode.value === 'baike') {
    path = '/api/roles/create-with-baike'
    form.append('baike_query', baikeQuery.value.trim())
  }

  creating.value = true
  actionStatus.value = '正在创建角色与音色，请稍候...'
  try {
    const response = await fetch(apiUrl(path), { method: 'POST', body: form })
    if (!response.ok) throw new Error(await readError(response, '创建角色失败'))
    actionStatus.value = `角色“${name}”已创建`
    resetCreateForm()
    await loadRoles(name)
  } catch (error) {
    actionStatus.value = error.message || '创建角色失败'
  } finally {
    creating.value = false
  }
}

async function deleteSelectedRole() {
  if (!selectedRole.value) return
  const ok = await openConfirm({
    title: '删除角色',
    message: `确定删除角色“${selectedRole.value}”吗？该操作会同时清理对应音色与参考音频。`,
    danger: true,
  })
  if (!ok) return

  const deletingName = selectedRole.value
  deleting.value = true
  actionStatus.value = `正在删除“${deletingName}”...`
  try {
    const response = await fetch(apiUrl(`/api/roles/${encodeURIComponent(deletingName)}`), { method: 'DELETE' })
    if (!response.ok) throw new Error(await readError(response, '删除角色失败'))
    actionStatus.value = `角色“${deletingName}”已删除`
    detailDrawerOpen.value = false
    await loadRoles()
  } catch (error) {
    actionStatus.value = error.message || '删除角色失败'
  } finally {
    deleting.value = false
  }
}

onMounted(() => Promise.all([loadRoles(), loadVoiceCapabilities()]))
</script>

<template>
  <main class="role-manager-page">
    <TitleBar />
    <ConfirmDialog />
    <header class="role-manager-header">
      <div>
        <p class="page-eyebrow">Virtual Companion</p>
        <h1>角色管理</h1>
        <p class="page-summary">创建角色、查看其人格设定与音色配置，在这里集中完成管理。</p>
      </div>
      <a class="btn btn-secondary" href="./">返回陪伴空间</a>
    </header>

    <p v-if="actionStatus" class="role-action-status" aria-live="polite">{{ actionStatus }}</p>

    <section class="role-manager-grid">
      <section class="role-create-panel">
        <div class="section-heading">
          <div>
            <p class="section-kicker">新角色</p>
            <h2>创建陪伴者</h2>
          </div>
        </div>

        <form class="role-create-form" @submit.prevent="createRole">
          <label>
            <span>角色名称</span>
            <input v-model="roleName" maxlength="50" placeholder="例如：芙宁娜" />
          </label>

          <fieldset class="creation-mode">
            <legend>生成方式</legend>
            <label :class="{ active: createMode === 'manual' }"><input v-model="createMode" value="manual" type="radio" />手写设定</label>
            <label :class="{ active: createMode === 'auto' }"><input v-model="createMode" value="auto" type="radio" />自动生成</label>
            <label :class="{ active: createMode === 'baike' }"><input v-model="createMode" value="baike" type="radio" />百科参考</label>
          </fieldset>

          <fieldset class="creation-mode voice-engine-mode">
            <legend>语音引擎</legend>
            <label :class="{ active: voiceProvider === 'cosyvoice', unavailable: !cosyVoiceAvailable }">
              <input v-model="voiceProvider" value="cosyvoice" type="radio" :disabled="!cosyVoiceAvailable" />
              <strong>CosyVoice</strong>
              <small>{{ cosyVoiceAvailable ? '可用' : '缺少 OSS 配置' }}</small>
            </label>
            <label :class="{ active: voiceProvider === 'qwen_tts', unavailable: !qwenAvailable }">
              <input v-model="voiceProvider" value="qwen_tts" type="radio" :disabled="!qwenAvailable" />
              <strong>Qwen3-TTS</strong>
              <small>{{ qwenAvailable ? '可用' : '缺少 DashScope Key' }}</small>
            </label>
          </fieldset>
          <div class="voice-provider-state" :class="{ warning: !selectedProviderAvailable }">
            <span>{{ providerHint }}</span>
            <a v-if="!selectedProviderAvailable" href="settings.html">前往设置</a>
          </div>

          <label v-if="createMode === 'manual'">
            <span>角色提示词</span>
            <textarea v-model="systemPrompt" placeholder="描述角色的性格、口吻、关系边界与世界观"></textarea>
          </label>
          <label v-if="createMode === 'baike'">
            <span>百科词条或链接</span>
            <input v-model="baikeQuery" placeholder="留空时将用角色名称搜索" />
          </label>

          <label class="audio-picker">
            <span>参考音频</span>
            <input accept="audio/*" type="file" @change="selectAudio" />
            <small>{{ audioLabel }}</small>
          </label>

          <button class="btn btn-primary create-submit" :disabled="!createFormReady || creating" type="submit">
            {{ createButtonLabel }}
          </button>
        </form>
      </section>

      <aside class="role-directory" aria-label="角色列表">
        <div class="section-heading">
          <div>
            <p class="section-kicker">角色库</p>
            <h2>{{ roles.length }} 位角色</h2>
          </div>
          <button class="icon-button" title="刷新角色列表" type="button" @click="loadRoles()">&#8635;</button>
        </div>
        <p v-if="listStatus" class="empty-state">{{ listStatus }}</p>
        <div v-else-if="roles.length" class="role-directory-list">
          <button
            v-for="name in roles"
            :key="name"
            class="directory-role"
            :class="{ selected: name === selectedRole }"
            type="button"
            @click="openRoleDetail(name)"
          >
            <span class="role-avatar">{{ name.slice(0, 1) }}</span>
            <span class="directory-role-copy">
              <strong>{{ name }}</strong>
              <small>{{ roleSummaries[name]?.voice_id ? '音色已配置' : '点击查看配置' }}</small>
            </span>
          </button>
        </div>
        <p v-else class="empty-state">还没有角色，创建第一位陪伴者吧。</p>
      </aside>
    </section>

    <div v-if="detailDrawerOpen" class="role-drawer-backdrop" @click.self="detailDrawerOpen = false">
      <aside class="role-detail-drawer" aria-label="角色详情" aria-live="polite">
        <div class="detail-title-row">
          <div>
            <p class="section-kicker">角色配置</p>
            <h2>{{ roleDetail?.role_name || selectedRole }}</h2>
          </div>
          <button class="icon-button" title="关闭详情" type="button" @click="detailDrawerOpen = false">&times;</button>
        </div>

        <p v-if="detailStatus" class="empty-state">{{ detailStatus }}</p>
        <template v-else-if="roleDetail">
          <dl class="role-metadata">
            <div>
              <dt>音色状态</dt>
              <dd><span class="status-dot"></span>{{ roleDetail.voice_id ? '已配置复刻音色' : '未配置音色' }}</dd>
            </div>
            <div>
              <dt>创建时间</dt>
              <dd>{{ roleDetail.timestamp || '未记录' }}</dd>
            </div>
            <div>
              <dt>语音模型</dt>
              <dd>{{ roleDetail.target_model || '未记录' }}</dd>
            </div>
            <div>
              <dt>语音引擎</dt>
              <dd>{{ roleDetail.voice_provider === 'qwen_tts' ? 'Qwen3-TTS' : roleDetail.voice_provider === 'cosyvoice' ? 'CosyVoice' : '旧配置（自动识别）' }}</dd>
            </div>
            <div>
              <dt>参考音频</dt>
              <dd class="path-value">{{ roleDetail.reference_audio_path || '未记录' }}</dd>
            </div>
          </dl>

          <details class="prompt-details">
            <summary>查看人格提示词</summary>
            <p>{{ roleDetail.system_prompt || '暂无人格设定' }}</p>
          </details>

          <button class="btn btn-danger" :disabled="deleting" type="button" @click="deleteSelectedRole">
            {{ deleting ? '删除中...' : '删除角色' }}
          </button>
        </template>
      </aside>
    </div>
  </main>
</template>
