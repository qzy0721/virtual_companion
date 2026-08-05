<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm'
import { VRMAnimationLoaderPlugin, createVRMAnimationClip } from '@pixiv/three-vrm-animation'
import { apiUrl } from '../api'

const props = defineProps({
  pet: { type: Boolean, default: false },
})
const emit = defineEmits(['ready'])

const container = ref(null)
const stageStatus = ref('正在加载模型...')
const currentExpression = ref('neutral')
const currentIntensity = ref(0)
const loaded = ref(false)

const DEFAULT_EXPRESSIONS = ['happy', 'sad', 'angry', 'surprised', 'relaxed', 'neutral']
const ACTION_LIST = ['angry', 'blush', 'clapping', 'goodbye', 'jump', 'look_around', 'relax', 'sad', 'sleepy', 'surprised']
const ACTION_FILES = {
  angry: 'Angry', blush: 'Blush', clapping: 'Clapping', goodbye: 'Goodbye', jump: 'Jump',
  look_around: 'LookAround', relax: 'Relax', sad: 'Sad', sleepy: 'Sleepy', surprised: 'Surprised',
}
const AVATAR_FACING_ROTATION = Math.PI

let scene = null
let camera = null
let renderer = null
let controls = null
let vrm = null
let avatarRoot = null
let mixer = null
let animationFrame = null
let resizeObserver = null
let blinkTimer = null
let vrmExpressions = []
let vrmaClips = {}
let currentAction = null
let idleAction = null
let idleClip = null
let idleActionName = 'idle'
let actionPlaying = false
let restPose = {}
let activeExpression = null
let activeWeight = 0
let targetWeight = 0
let lastFrame = performance.now()

function assetUrl(path) {
  return `${import.meta.env.BASE_URL}${path}`
}

function setupScene() {
  scene = new THREE.Scene()
  camera = new THREE.PerspectiveCamera(40, 1, 0.1, 20)
  camera.position.set(0, 1.4, 3.2)
  camera.lookAt(0, 1.2, 0)

  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
  // WebGL 保持透明，舞台底色交给 CSS 的 --bg-soft，从而自动跟随主题切换。
  renderer.setClearAlpha(0)
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, props.pet ? 1.5 : 2))
  container.value.appendChild(renderer.domElement)

  controls = new OrbitControls(camera, renderer.domElement)
  controls.target.set(0, 1.15, 0)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.minDistance = 1.5
  controls.maxDistance = 5
  controls.maxPolarAngle = Math.PI * 0.7
  controls.update()

  scene.add(new THREE.AmbientLight(0xffffff, 0.9))
  const keyLight = new THREE.DirectionalLight(0xffffff, 1)
  keyLight.position.set(0.5, 1.5, 2)
  scene.add(keyLight)
  const rimLight = new THREE.DirectionalLight(0x8899ff, 0.4)
  rimLight.position.set(-0.5, 0.3, -1)
  scene.add(rimLight)

  resizeObserver = new ResizeObserver(resize)
  resizeObserver.observe(container.value)
  blinkTimer = window.setInterval(blink, 3500)
  animate()
}

function resize() {
  if (!container.value || !camera || !renderer) return
  const { clientWidth, clientHeight } = container.value
  if (!clientWidth || !clientHeight) return
  camera.aspect = clientWidth / clientHeight
  camera.updateProjectionMatrix()
  renderer.setSize(clientWidth, clientHeight, false)
}

function animate() {
  animationFrame = requestAnimationFrame(animate)
  const now = performance.now()
  const delta = Math.min((now - lastFrame) / 1000, 0.1)
  lastFrame = now

  if (activeExpression && vrm?.expressionManager) {
    activeWeight += (targetWeight - activeWeight) * 0.1
    if (Math.abs(targetWeight - activeWeight) < 0.005) activeWeight = targetWeight
    if (activeWeight > 0.001) vrm.expressionManager.setValue(activeExpression, activeWeight)
    else if (targetWeight === 0) {
      vrm.expressionManager.setValue(activeExpression, 0)
      activeExpression = null
    }
  }

  if (vrm && avatarRoot && !actionPlaying && !idleAction?.isRunning()) {
    for (const [name, pose] of Object.entries(restPose)) {
      vrm.scene.getObjectByName(name)?.quaternion.slerp(pose.quaternion, 0.08)
    }
    avatarRoot.position.y = Math.sin(now / 1000 * 2.5) * 0.004
  } else if (vrm && avatarRoot && actionPlaying) {
    avatarRoot.position.set(0, 0, 0)
  }

  mixer?.update(delta)
  vrm?.update(delta)
  if (vrm) syncNormalizedBones()
  controls?.update()
  renderer?.render(scene, camera)
}

function syncNormalizedBones() {
  for (const name of Object.keys(restPose)) {
    const normalized = vrm.scene.getObjectByName(name)
    const original = vrm.scene.getObjectByName(name.replace('Normalized_', ''))
    if (normalized && original) {
      original.position.copy(normalized.position)
      original.quaternion.copy(normalized.quaternion)
    }
  }
}

function createVrmLoader() {
  const loader = new GLTFLoader()
  loader.register((parser) => new VRMLoaderPlugin(parser))
  return loader
}

async function loadDefault() {
  return loadUrl(assetUrl('assets/胡桃.vrm'))
}

async function loadUrl(url) {
  stageStatus.value = '正在加载模型...'
  const loader = createVrmLoader()
  const gltf = await new Promise((resolve, reject) => {
    loader.load(
      url,
      resolve,
      (progress) => {
        if (progress.total) stageStatus.value = `正在加载模型 ${Math.round(progress.loaded / progress.total * 100)}%`
      },
      reject,
    )
  })
  const nextVrm = gltf.userData?.vrm
  if (!nextVrm) throw new Error('VRM 数据无效')
  await attachVrm(nextVrm, gltf.scene)
  const capabilities = { expressions: getExpressions(), actions: getActions() }
  emit('ready', capabilities)
  return capabilities
}

async function loadFile(file) {
  stageStatus.value = '正在切换模型...'
  const buffer = await file.arrayBuffer()
  const loader = createVrmLoader()
  const gltf = await new Promise((resolve, reject) => loader.parse(buffer, '', resolve, reject))
  const nextVrm = gltf.userData?.vrm
  if (!nextVrm) throw new Error('这不是有效的 VRM 文件')
  await attachVrm(nextVrm, gltf.scene)
  return { expressions: getExpressions(), fileName: file.name }
}

async function attachVrm(nextVrm, gltfScene) {
  disposeVrm()
  VRMUtils.removeUnnecessaryJoints(gltfScene)
  vrm = nextVrm
  avatarRoot = new THREE.Group()
  avatarRoot.rotation.y = AVATAR_FACING_ROTATION
  avatarRoot.add(vrm.scene)
  scene.add(avatarRoot)
  if (vrm.humanoid) vrm.humanoid.autoUpdateHumanBones = false
  mixer = new THREE.AnimationMixer(vrm.scene)
  vrmaClips = {}
  currentAction = null
  idleAction = null
  idleClip = null
  actionPlaying = false
  activeExpression = null
  activeWeight = 0
  targetWeight = 0
  currentExpression.value = 'neutral'
  currentIntensity.value = 0
  readExpressions()
  captureRestPose()
  await loadAnimations()
  loaded.value = true
  stageStatus.value = ''
}

function disposeVrm() {
  if (!vrm) return
  if (avatarRoot) {
    scene.remove(avatarRoot)
    avatarRoot.remove(vrm.scene)
  } else {
    scene.remove(vrm.scene)
  }
  vrm.scene.traverse((object) => {
    object.geometry?.dispose()
    if (Array.isArray(object.material)) object.material.forEach((material) => material.dispose())
    else object.material?.dispose()
  })
  vrm = null
  avatarRoot = null
  mixer = null
  restPose = {}
}

function readExpressions() {
  const manager = vrm?.expressionManager
  const names = manager?._expressionMap ? Object.keys(manager._expressionMap) : Object.keys(manager?.expressionMap || {})
  vrmExpressions = names.length ? names : [...DEFAULT_EXPRESSIONS]
}

function captureRestPose() {
  restPose = {}
  vrm.scene.traverse((object) => {
    if (object.name?.startsWith?.('Normalized_') && object.quaternion && object.name.includes('Arm')) {
      restPose[object.name] = { quaternion: object.quaternion.clone() }
    }
  })
  for (const name of ['Normalized_13joint_HipMaster', 'Normalized_18joint_Head']) {
    const bone = vrm.scene.getObjectByName(name)
    if (bone) restPose[name] = { quaternion: bone.quaternion.clone() }
  }
}

async function loadAnimations() {
  if (!vrm) return
  const loader = new GLTFLoader()
  loader.register((parser) => new VRMAnimationLoaderPlugin(parser))
  for (const action of ACTION_LIST) {
    const clip = await loadAnimation(loader, assetUrl(`assets/animations/${ACTION_FILES[action]}.vrma`))
    if (clip) vrmaClips[action] = clip
  }
  idleClip = await loadAnimation(loader, assetUrl('assets/animations/Idle.vrma'))
  if (idleClip) vrmaClips.idle = idleClip
  idleClip = vrmaClips[idleActionName] || vrmaClips.idle || null
  startIdle()
}

async function loadCompanionPreferences() {
  try {
    const response = await fetch(apiUrl('/api/pet/preferences'))
    if (!response.ok) return
    const preferences = await response.json()
    idleActionName = normalizeActionName(preferences.idle_action || 'idle')
  } catch {
    idleActionName = 'idle'
  }
}

function loadAnimation(loader, url) {
  return new Promise((resolve) => {
    loader.load(
      url,
      (gltf) => {
        try {
          const animation = gltf.userData.vrmAnimations?.[0]
          resolve(animation ? createVRMAnimationClip(animation, vrm) : null)
        } catch {
          resolve(null)
        }
      },
      undefined,
      () => resolve(null),
    )
  })
}

function normalizeActionName(name) {
  return String(name || 'none')
    .trim()
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/[^A-Za-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .toLowerCase() || 'none'
}

function resolveActionName(name) {
  const normalizedName = normalizeActionName(name)
  const compactName = normalizedName.replaceAll('_', '')
  return ACTION_LIST.find((action) => (
    action === normalizedName || action.replaceAll('_', '') === compactName
  )) || normalizedName
}

function playAction(name) {
  const normalizedName = resolveActionName(name)
  if (!mixer || !vrmaClips[normalizedName]) return false
  currentAction?.fadeOut(0.2)
  stopIdle()
  const nextAction = mixer.clipAction(vrmaClips[normalizedName])
  nextAction.reset().setLoop(THREE.LoopOnce).clampWhenFinished = true
  const onFinished = (event) => {
    if (event.action !== nextAction) return
    mixer.removeEventListener('finished', onFinished)
    actionPlaying = false
    currentAction = null
    startIdle()
  }
  mixer.addEventListener('finished', onFinished)
  nextAction.fadeIn(0.3).play()
  currentAction = nextAction
  actionPlaying = true
  return true
}

function stopAction() {
  currentAction?.fadeOut(0.25)
  currentAction = null
  actionPlaying = false
  startIdle()
}

function startIdle() {
  if (!mixer || !idleClip || idleAction?.isRunning()) return
  idleAction = mixer.clipAction(idleClip)
  idleAction.reset().setLoop(THREE.LoopRepeat).clampWhenFinished = false
  idleAction.fadeIn(0.5).play()
}

function stopIdle() {
  if (idleAction?.isRunning()) idleAction.fadeOut(0.3)
}

function setIdleAction(name) {
  const normalizedName = resolveActionName(name)
  idleActionName = vrmaClips[normalizedName] ? normalizedName : 'idle'
  idleAction?.stop()
  idleAction = null
  idleClip = vrmaClips[idleActionName] || vrmaClips.idle || null
  if (!actionPlaying) startIdle()
  return Boolean(idleClip)
}

function setExpression(name, intensity = 0.8) {
  if (!vrm?.expressionManager || !vrmExpressions.length || !name || name.toLowerCase() === 'neutral') {
    clearExpression()
    return
  }
  const matched = vrmExpressions.find((expression) => expression.toLowerCase() === name.toLowerCase())
  if (!matched) {
    clearExpression()
    return
  }
  if (activeExpression && activeExpression !== matched) vrm.expressionManager.setValue(activeExpression, 0)
  activeExpression = matched
  targetWeight = intensity >= 0.5 ? 1 : 0
  currentExpression.value = matched
  currentIntensity.value = intensity
}

function clearExpression() {
  if (activeExpression && vrm?.expressionManager) vrm.expressionManager.setValue(activeExpression, 0)
  activeExpression = null
  activeWeight = 0
  targetWeight = 0
  currentExpression.value = 'neutral'
  currentIntensity.value = 0
}

function setMouthOpen(value) {
  if (!vrm?.expressionManager) return
  const mouth = vrmExpressions.find((expression) => expression.toLowerCase() === 'aa')
  if (mouth) vrm.expressionManager.setValue(mouth, Math.max(0, Math.min(1, value)))
}

function blink() {
  const blinkName = vrmExpressions.find((expression) => expression.toLowerCase() === 'blink')
  if (!blinkName || !vrm?.expressionManager) return
  vrm.expressionManager.setValue(blinkName, 1)
  window.setTimeout(() => vrm?.expressionManager?.setValue(blinkName, 0), 150)
}

function getExpressions() {
  const nonEmotion = ['aa', 'ih', 'ou', 'ee', 'oh', 'a', 'i', 'u', 'e', 'o', 'blink', 'look', 'lookup', 'lookdown', 'lookleft', 'lookright']
  const expressions = vrmExpressions.filter((expression) => !nonEmotion.includes(expression.toLowerCase()))
  return expressions.length ? expressions : [...DEFAULT_EXPRESSIONS]
}

function getActions() {
  return ['none', ...ACTION_LIST.filter((action) => Boolean(vrmaClips[action]))]
}

onMounted(async () => {
  await loadCompanionPreferences()
  setupScene()
  try {
    await loadDefault()
  } catch (error) {
    stageStatus.value = '模型加载失败'
  }
})

onBeforeUnmount(() => {
  cancelAnimationFrame(animationFrame)
  resizeObserver?.disconnect()
  window.clearInterval(blinkTimer)
  controls?.dispose()
  disposeVrm()
  renderer?.dispose()
})

defineExpose({ loadUrl, loadFile, getExpressions, getActions, setExpression, clearExpression, setMouthOpen, playAction, stopAction, setIdleAction })
</script>

<template>
  <div class="companion-stage" :class="{ 'pet-stage': props.pet }">
    <div ref="container" class="stage-canvas"></div>
    <div v-if="loaded && !props.pet" class="stage-overlay">
      <span class="expression-name">{{ currentExpression }}</span>
      <span class="expression-value">{{ currentIntensity.toFixed(2) }}</span>
    </div>
    <div v-if="stageStatus && !props.pet" class="stage-status">{{ stageStatus }}</div>
  </div>
</template>
