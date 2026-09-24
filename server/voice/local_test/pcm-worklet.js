// Downsample the browser's actual capture rate to 16 kHz mono PCM.
class PCMRecorder extends AudioWorkletProcessor {
  constructor({processorOptions = {}} = {}) {
    super();
    this.phase = 0;
    this.sum = 0;
    this.count = 0;
    this.pending = [];
    this.peak = 0;
    this.active = true;
    this.maxSamples = processorOptions.maxSamples ?? 480000;
    this.totalSamples = 0;
    this.port.onmessage = ({data}) => {
      if (data === "stop") {
        this.active = false;
        this.flush();
        this.port.postMessage({type: "stopped"});
      }
    };
  }
  flush() {
    if (!this.pending.length) return;
    const pcm = new Int16Array(this.pending);
    this.pending = [];
    this.port.postMessage({type: "pcm", buffer: pcm.buffer}, [pcm.buffer]);
    this.port.postMessage({type: "level", value: this.peak});
    this.peak = 0;
  }
  process(inputs) {
    const samples = inputs[0]?.[0];
    if (!this.active || !samples) return true;
    for (const sample of samples) {
      this.peak = Math.max(this.peak, Math.abs(sample));
      this.sum += sample;
      this.count++;
      this.phase += 16000;
      if (this.phase >= sampleRate) {
        const value = Math.max(-1, Math.min(1, this.sum / this.count));
        this.pending.push(Math.round(value * (value < 0 ? 32768 : 32767)));
        this.phase -= sampleRate;
        this.sum = 0;
        this.count = 0;
        if (this.pending.length === 960) this.flush();
        if (this.maxSamples > 0 && ++this.totalSamples >= this.maxSamples) {
          this.active = false;
          this.flush();
          this.port.postMessage({type: "limit"});
          break;
        }
      }
    }
    return true;
  }
}
registerProcessor("pcm-recorder", PCMRecorder);
