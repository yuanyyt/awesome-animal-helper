class PcmRecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.input = [];
    this.position = 0;
    this.output = [];
    this.port.onmessage = (event) => {
      if (event.data?.type === "flush") {
        this.emit(true);
        this.port.postMessage({ type: "flushed" });
      }
    };
  }

  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel?.length) return true;
    this.input.push(...channel);
    while (this.position < this.input.length - 1) {
      const left = Math.floor(this.position);
      const fraction = this.position - left;
      const sample = this.input[left] * (1 - fraction) + this.input[left + 1] * fraction;
      this.output.push(Math.max(-1, Math.min(1, sample)));
      this.position += this.ratio;
    }
    const consumed = Math.floor(this.position);
    if (consumed > 0) {
      this.input = this.input.slice(consumed);
      this.position -= consumed;
    }
    this.emit(false);
    return true;
  }

  emit(flush) {
    const frameSamples = 1600;
    while (this.output.length >= frameSamples || (flush && this.output.length)) {
      const count = flush ? Math.min(frameSamples, this.output.length) : frameSamples;
      const samples = this.output.splice(0, count);
      const pcm = new Int16Array(samples.length);
      for (let index = 0; index < samples.length; index += 1) {
        const value = samples[index];
        pcm[index] = value < 0 ? value * 0x8000 : value * 0x7fff;
      }
      this.port.postMessage({ type: "pcm", buffer: pcm.buffer }, [pcm.buffer]);
    }
  }
}

registerProcessor("pcm-recorder", PcmRecorderProcessor);
