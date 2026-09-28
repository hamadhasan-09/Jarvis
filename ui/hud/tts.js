const EVENTS = Object.freeze(["start", "end", "error"]);

const FEMALE_VOICE_NAMES = /female|woman|girl|jenny|zira|aria|samantha|susan|hazel|eva|sara|swara|uzma|google uk english female/i;
const MALE_VOICE_NAMES = /male|man|guy|david|mark|ryan|daniel|george|madhur|asad|google uk english male/i;

export class TtsInterface {
  constructor({ preferences }) {
    this.preferences = preferences;
    this.synth = "speechSynthesis" in window ? window.speechSynthesis : null;
    this.voices = [];
    this.currentText = "";
    this.currentUtterance = null;
    this.speaking = false;
    this.listeners = Object.fromEntries(EVENTS.map((event) => [event, new Set()]));
    this.onVoicesChanged = () => this.refreshVoices();
    if (this.synth && "onvoiceschanged" in this.synth) {
      this.synth.addEventListener("voiceschanged", this.onVoicesChanged);
    }
    this.refreshVoices();
  }

  on(eventName, listener) {
    if (!this.listeners[eventName]) return () => {};
    this.listeners[eventName].add(listener);
    return () => this.listeners[eventName].delete(listener);
  }

  emit(eventName, payload) {
    for (const listener of this.listeners[eventName] || []) listener(payload);
  }

  refreshVoices() {
    this.voices = this.synth && this.synth.getVoices ? this.synth.getVoices() : [];
    return this.voices;
  }

  unlock() {
    if (!this.synth || typeof SpeechSynthesisUtterance === "undefined") return;
    try {
      const warmup = new SpeechSynthesisUtterance("");
      warmup.volume = 0;
      this.synth.speak(warmup);
      this.synth.cancel();
    } catch (_) { /* Some browsers do not require a warmup utterance. */ }
  }

  cancel() {
    if (this.synth) this.synth.cancel();
    this.currentUtterance = null;
    this.currentText = "";
    this.speaking = false;
  }

  #selectVoice(languageCode) {
    const languageVoices = this.voices.filter((item) => new RegExp(`^${languageCode}`, "i").test(item.lang || ""));
    const candidates = languageVoices.length ? languageVoices : this.voices;
    if (this.preferences.gender === "auto") return candidates[0] || null;
    const pattern = this.preferences.gender === "female" ? FEMALE_VOICE_NAMES : MALE_VOICE_NAMES;
    return candidates.find((item) => pattern.test(item.name || "")) || candidates[0] || null;
  }

  speak(text, attempt = 0) {
    const value = String(text || "").trim();
    if (!value) return false;
    if (!this.synth || typeof SpeechSynthesisUtterance === "undefined") {
      this.emit("error", { error: "unsupported", text: value });
      return false;
    }
    this.refreshVoices();
    if (!this.voices.length && attempt < 6) {
      window.setTimeout(() => this.speak(value, attempt + 1), 250);
      return true;
    }

    try {
      this.synth.cancel();
      const utterance = new SpeechSynthesisUtterance(value);
      const output = this.preferences.outputLanguageDetails(value);
      utterance.rate = 1.04;
      utterance.pitch = this.preferences.gender === "female" ? 1.14 : (this.preferences.gender === "male" ? 0.88 : 1);
      utterance.volume = 1;
      utterance.lang = output.speechLang;
      const voice = this.#selectVoice(output.voiceLang);
      if (voice) utterance.voice = voice;
      this.currentUtterance = utterance;
      this.currentText = value;
      utterance.onstart = () => {
        if (this.currentUtterance !== utterance) return;
        this.speaking = true;
        this.emit("start", { text: value });
      };
      utterance.onend = () => {
        if (this.currentUtterance !== utterance) return;
        this.speaking = false;
        this.currentUtterance = null;
        this.currentText = "";
        this.emit("end", { text: value });
      };
      utterance.onerror = (event) => {
        if (this.currentUtterance !== utterance) return;
        this.speaking = false;
        this.currentUtterance = null;
        this.currentText = "";
        this.emit("error", { error: event.error, text: value });
      };
      this.synth.speak(utterance);
      return true;
    } catch (error) {
      this.speaking = false;
      this.currentUtterance = null;
      this.currentText = "";
      this.emit("error", { error: String(error), text: value });
      return false;
    }
  }

  get isSpeaking() {
    return this.speaking;
  }

  destroy() {
    this.cancel();
    if (this.synth && "onvoiceschanged" in this.synth) {
      this.synth.removeEventListener("voiceschanged", this.onVoicesChanged);
    }
    for (const listeners of Object.values(this.listeners)) listeners.clear();
  }
}
