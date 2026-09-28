import { clamp, damp } from "./math.js";

/**
 * Live microphone / future TTS energy. Levels are 0..1.
 * SpeechSynthesis cannot provide PCM; setExternalLevels is the stable hook
 * for a future TTS engine. Do not treat pose-only speaking motion as audio.
 */
export class AudioReactiveController {
  constructor() {
    this.context = null;
    this.analyser = null;
    this.source = null;
    this.stream = null;
    this.freq = null;
    this.time = null;
    this.externalRms = null;
    this.smoothed = { rms: 0, speech: 0, bass: 0 };
    this.live = false;
  }

  get levels() {
    return { ...this.smoothed, live: this.live, external: this.externalRms != null };
  }

  setExternalLevels(rms) {
    this.externalRms = rms == null ? null : clamp(Number(rms) || 0, 0, 1);
  }

  async attachStream(stream) {
    if (this.stream === stream && this.analyser) return true;
    this.detach({ stopTracks: this.stream !== stream });
    if (!stream || typeof AudioContext === "undefined") return false;
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!Context) return false;
    this.stream = stream;
    this.context = new Context();
    if (this.context.state === "suspended") {
      try { await this.context.resume(); } catch (_) { /* autoplay policy */ }
    }
    this.analyser = this.context.createAnalyser();
    this.analyser.fftSize = 1024;
    this.analyser.smoothingTimeConstant = 0.72;
    this.source = this.context.createMediaStreamSource(stream);
    this.source.connect(this.analyser);
    this.freq = new Uint8Array(this.analyser.frequencyBinCount);
    this.time = new Uint8Array(this.analyser.fftSize);
    this.live = true;
    return true;
  }

  sample(dtSeconds) {
    let rms = 0;
    let speech = 0;
    let bass = 0;
    if (this.analyser && this.freq && this.time) {
      this.analyser.getByteTimeDomainData(this.time);
      this.analyser.getByteFrequencyData(this.freq);
      let sum = 0;
      for (let i = 0; i < this.time.length; i += 1) {
        const centered = (this.time[i] - 128) / 128;
        sum += centered * centered;
      }
      rms = Math.sqrt(sum / this.time.length);
      const speechBins = this.freq.subarray(8, 48);
      let speechSum = 0;
      for (let i = 0; i < speechBins.length; i += 1) speechSum += speechBins[i];
      speech = speechSum / (speechBins.length * 255);
      let bassSum = 0;
      for (let i = 1; i < 8; i += 1) bassSum += this.freq[i];
      bass = bassSum / (7 * 255);
      this.live = true;
    } else if (this.externalRms != null) {
      rms = this.externalRms;
      speech = this.externalRms;
      this.live = false;
    } else {
      this.live = false;
    }
    const attack = rms > this.smoothed.rms ? 18 : 7;
    this.smoothed.rms = damp(this.smoothed.rms, clamp(rms * 2.4, 0, 1), attack, dtSeconds);
    this.smoothed.speech = damp(this.smoothed.speech, clamp(speech * 1.8, 0, 1), 14, dtSeconds);
    this.smoothed.bass = damp(this.smoothed.bass, clamp(bass, 0, 1), 10, dtSeconds);
    return this.levels;
  }

  detach({ stopTracks = true } = {}) {
    this.live = false;
    if (this.source) {
      try { this.source.disconnect(); } catch (_) { /* already disconnected */ }
    }
    this.source = null;
    this.analyser = null;
    this.freq = null;
    this.time = null;
    if (this.context) {
      const closing = this.context;
      this.context = null;
      closing.close().catch(() => {});
    }
    if (stopTracks && this.stream) {
      this.stream.getTracks().forEach((track) => track.stop());
    }
    this.stream = null;
  }
}
