class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super()
    const settings = options.processorOptions || {}
    this.targetSampleRate = settings.targetSampleRate || 16000
    this.frameSamples = Math.max(1, Math.round(this.targetSampleRate * (settings.frameMs || 100) / 1000))
    this.sourceStep = sampleRate / this.targetSampleRate
    this.nextSourceIndex = 0
    this.frame = new Int16Array(this.frameSamples)
    this.frameOffset = 0

    this.port.onmessage = (event) => {
      if (event.data?.type === 'flush') {
        this.flush()
        this.port.postMessage({ type: 'flushed' })
      }
    }
  }

  process(inputs) {
    const input = inputs[0]?.[0]
    if (!input?.length) return true

    let sourceIndex = this.nextSourceIndex
    while (sourceIndex < input.length) {
      const before = Math.floor(sourceIndex)
      const after = Math.min(before + 1, input.length - 1)
      const fraction = sourceIndex - before
      this.append(input[before] + (input[after] - input[before]) * fraction)
      sourceIndex += this.sourceStep
    }
    this.nextSourceIndex = sourceIndex - input.length
    return true
  }

  append(sample) {
    const clipped = Math.max(-1, Math.min(1, sample))
    this.frame[this.frameOffset++] = clipped < 0 ? clipped * 0x8000 : clipped * 0x7fff
    if (this.frameOffset === this.frame.length) this.flush()
  }

  flush() {
    if (!this.frameOffset) return
    const chunk = this.frame.slice(0, this.frameOffset)
    this.frameOffset = 0
    this.port.postMessage({ type: 'pcm', data: chunk.buffer }, [chunk.buffer])
  }
}

registerProcessor('pcm-capture-processor', PcmCaptureProcessor)
