// 轻量能量 VAD：对 16k/40ms PCM 帧做自适应噪声底与滞回判断。
// 浏览器已开启 AEC，扬声器回声通常会被压到噪声底附近；角色说话时使用
// barge 模式（更高的门限和更长的确认窗口），避免 TTS 尾音误触发打断。

const DEFAULTS = {
  frameMs: 40,
  startRms: 0.0085,
  sustainRms: 0.0055,
  noiseStartRatio: 2.6,
  noiseSustainRatio: 1.8,
  bargeStartRms: 0.013,
  bargeStartRatio: 3.4,
  startFrames: 3,
  bargeStartFrames: 5,
  silenceMs: 520,
  maxSpeechMs: 20000,
  noiseFloorMin: 0.0012,
  noiseFloorMax: 0.045,
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
  }

  push(rms) {
    const value = Number.isFinite(rms) ? Math.max(0, rms) : 0
    this.lastRms = value
    const frameMs = this.options.frameMs

    if (!this.speaking) {
      // 未说话时缓慢跟踪环境噪声：变安静时快、变吵时慢，避免空调/风扇把门限抬高。
      const alpha = value < this.noiseFloor ? 0.12 : 0.015
      this.noiseFloor = clamp(
        this.noiseFloor + (value - this.noiseFloor) * alpha,
        this.options.noiseFloorMin,
        this.options.noiseFloorMax,
      )
      const startGate = this.bargeMode
        ? Math.max(this.options.bargeStartRms, this.noiseFloor * this.options.bargeStartRatio)
        : Math.max(this.options.startRms, this.noiseFloor * this.options.noiseStartRatio)
      if (value >= startGate) {
        this.speechRun += 1
        this.silenceMs = 0
        const needed = this.bargeMode ? this.options.bargeStartFrames : this.options.startFrames
        if (this.speechRun >= needed) {
          this.speaking = true
          this.speechMs = this.speechRun * frameMs
          return { type: 'speech-start', rms: value, speechMs: this.speechMs }
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
    if (value >= sustainGate) this.silenceMs = 0
    else this.silenceMs += frameMs

    if (this.silenceMs >= this.options.silenceMs || this.speechMs >= this.options.maxSpeechMs) {
      const event = {
        type: 'speech-end',
        speechMs: this.speechMs,
        silenceMs: this.silenceMs,
        rms: value,
        forced: this.speechMs >= this.options.maxSpeechMs,
      }
      this.speaking = false
      this.speechRun = 0
      this.speechMs = 0
      this.silenceMs = 0
      return event
    }
    return null
  }
}
