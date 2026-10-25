// 轻量能量 VAD：对 16k/40ms PCM 帧做自适应噪声底与滞回判断。
// 浏览器已开启 AEC，扬声器回声通常会被压到噪声底附近；角色说话时使用
// barge 模式（更高的门限和更长的确认窗口），避免 TTS 尾音误触发打断。

const DEFAULTS = {
  frameMs: 40,
  startRms: 0.0085,
  sustainRms: 0.0055,
  noiseStartRatio: 2.6,
  noiseSustainRatio: 1.8,
  bargeStartRms: 0.014,
  bargeStartRatio: 3.2,
  startFrames: 3,
  bargeStartFrames: 5,
  silenceMs: 520,
  maxSpeechMs: 20000,
  noiseFloorMin: 0.0012,
  noiseFloorMax: 0.045,
  smoothFrames: 3,
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value))
}

export class VoiceActivityDetector {
  constructor(options = {}) {
    this.options = { ...DEFAULTS, ...options }
    this.bargeMode = false
    this.speaking = false
    this.speechRun = 0
    this.speechMs = 0
    this.silenceMs = 0
    this.noiseFloor = 0.002
    this.lastRms = 0
    this.energyHistory = []
  }

  setBargeMode(enabled) {
    this.bargeMode = Boolean(enabled)
  }

  reset() {
    this.speaking = false
    this.speechRun = 0
    this.speechMs = 0
    this.silenceMs = 0
    this.lastRms = 0
    this.energyHistory = []
  }

  push(rms) {
    const value = Number.isFinite(rms) ? Math.max(0, rms) : 0
    this.lastRms = value
    this.energyHistory.push(value)
    if (this.energyHistory.length > this.options.smoothFrames) this.energyHistory.shift()
    // 3 帧移动平均：语音中的自然能量低谷不会把确认计数清零，
    // 单个爆音也只能抬高平均值的 1/3，不容易骗过 VAD。
    const smoothed = this.energyHistory.reduce((total, item) => total + item, 0) / this.energyHistory.length
    const frameMs = this.options.frameMs

    if (!this.speaking) {
      // 未说话时缓慢跟踪环境噪声：变安静时快、变吵时慢，避免空调/风扇把门限抬高。
      const alpha = smoothed < this.noiseFloor ? 0.12 : 0.015
      this.noiseFloor = clamp(
        this.noiseFloor + (smoothed - this.noiseFloor) * alpha,
        this.options.noiseFloorMin,
        this.options.noiseFloorMax,
      )
      const startGate = this.bargeMode
        ? Math.max(this.options.bargeStartRms, this.noiseFloor * this.options.bargeStartRatio)
        : Math.max(this.options.startRms, this.noiseFloor * this.options.noiseStartRatio)
      if (smoothed >= startGate) {
        this.speechRun += 1
        this.silenceMs = 0
        const needed = this.bargeMode ? this.options.bargeStartFrames : this.options.startFrames
        if (this.speechRun >= needed) {
          this.speaking = true
          this.speechMs = this.speechRun * frameMs
          return { type: 'speech-start', rms: smoothed, speechMs: this.speechMs }
        }
      } else {
        this.speechRun = 0
        this.silenceMs += frameMs
      }
      return null
    }

    const sustainGate = Math.max(
      this.options.sustainRms,
      this.noiseFloor * this.options.noiseSustainRatio,
    )
    this.speechMs += frameMs
    if (smoothed >= sustainGate) this.silenceMs = 0
    else this.silenceMs += frameMs

    if (this.silenceMs >= this.options.silenceMs || this.speechMs >= this.options.maxSpeechMs) {
      const event = {
        type: 'speech-end',
        speechMs: this.speechMs,
        silenceMs: this.silenceMs,
        rms: smoothed,
        forced: this.speechMs >= this.options.maxSpeechMs,
      }
      this.speaking = false
      this.speechRun = 0
      this.speechMs = 0
      this.silenceMs = 0
      this.energyHistory = []
      return event
    }
    return null
  }
}
