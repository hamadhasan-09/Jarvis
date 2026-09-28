import { HUD_CONFIG } from "./config.js";

export class VoiceInterface {
  constructor({ dom, preferences, tts, audio, lifecycle, onUserSpeech, onFinalTranscript, onEnded, onStatus }) {
    this.dom = dom;
    this.preferences = preferences;
    this.tts = tts;
    this.audio = audio;
    this.lifecycle = lifecycle;
    this.onUserSpeech = onUserSpeech || (() => {});
    this.onFinalTranscript = onFinalTranscript || (() => {});
    this.onEnded = onEnded || (() => {});
    this.onStatus = onStatus || (() => {});
    this.recognition = null;
    this.recognitionActive = false;
    this.recognitionStarting = false;
    this.shouldListen = true;
    this.restartTimer = null;
    this.restartAttempts = 0;
    this.microphonePermissionState = "unknown";
    this.microphoneRequestPromise = null;
    this.microphoneStream = null;
    this.muted = false;
  }

  setPermissionGate(visible, copy, retryable = true) {
    const gate = this.dom.voicePermissionGate;
    const button = this.dom.voicePermissionButton;
    gate.hidden = !visible;
    if (copy) this.dom.voicePermissionCopy.textContent = copy;
    button.hidden = !retryable;
  }

  async requestPermission(fromGesture = false) {
    if (HUD_CONFIG.localVoiceMode) {
      this.microphonePermissionState = "local";
      return true;
    }

    if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== "function") {
      this.microphonePermissionState = "unsupported";
      this.setPermissionGate(true, "This browser does not expose microphone access. Use Chrome or Edge.", false);
      this.onStatus("BROWSER AUDIO LIMITED", "Use Chrome or Edge for live voice.", "error");
      return false;
    }
    if (this.microphonePermissionState === "granted" && this.microphoneStream) return true;
    if (this.microphonePermissionState === "denied" && !fromGesture) return false;
    if (this.microphoneRequestPromise) return this.microphoneRequestPromise;

    this.microphoneRequestPromise = navigator.mediaDevices.getUserMedia({ audio: true })
      .then(async (stream) => {
        this.microphoneStream = stream;
        this.microphonePermissionState = "granted";
        await this.audio.attachMicrophone(stream);
        this.setPermissionGate(false);
        return true;
      })
      .catch((error) => {
        this.microphonePermissionState = (error && (error.name === "NotAllowedError" || error.name === "PermissionDeniedError")) ? "denied" : "error";
        const message = this.microphonePermissionState === "denied"
          ? "Allow microphone access once; recognition will then start automatically."
          : "Microphone initialization failed temporarily. JARVIS will retry automatically.";
        this.setPermissionGate(true, message, true);
        this.onStatus(this.microphonePermissionState === "denied" ? "MICROPHONE ACCESS NEEDED" : "MICROPHONE ERROR", message, "error");
        return false;
      })
      .finally(() => { this.microphoneRequestPromise = null; });
    return this.microphoneRequestPromise;
  }

  async start(fromGesture = false) {
    if (HUD_CONFIG.localVoiceMode) {
      this.shouldListen = true;
      this.setPermissionGate(false);
      this.onStatus("LISTENING", "Local Windows voice channel is active.", "listening");
      return true;
    }

    if (this.recognitionActive || this.recognitionStarting || !this.shouldListen) return;
    const permitted = await this.requestPermission(fromGesture);
    if (!permitted || !this.shouldListen) return;
    this.setPermissionGate(false);
    this.#setupRecognition();
  }

  mute() {
    this.muted = true;
  }

  async unmute() {
    this.muted = false;
    this.setPermissionGate(false);
    await this.start(false);
  }

  restartForPreference() {
    clearTimeout(this.restartTimer);
    if (!this.recognition) {
      if (this.shouldListen) this.#setupRecognition();
      return;
    }
    try { this.recognition.stop(); } catch (_) { this.#scheduleRestart(); }
  }

  #setupRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      this.setPermissionGate(true, "Use Chrome or Edge to enable automatic voice recognition.", false);
      this.onStatus("BROWSER AUDIO LIMITED", "Use Chrome or Edge for live voice.", "error");
      return;
    }
    if (this.recognitionActive || this.recognitionStarting) return;
    clearTimeout(this.restartTimer);
    this.recognitionStarting = true;
    this.shouldListen = true;
    const instance = new SpeechRecognition();
    this.recognition = instance;
    instance.continuous = true;
    instance.interimResults = true;
    instance.lang = this.preferences.recognitionLanguage();
    instance.onstart = () => {
      if (this.recognition !== instance) return;
      this.recognitionActive = true;
      this.recognitionStarting = false;
      this.restartAttempts = 0;
      this.microphonePermissionState = "granted";
      this.setPermissionGate(false);
      this.dom.voiceModule.textContent = this.preferences.displayLabel();
      if (!this.tts.isSpeaking) this.onStatus("LISTENING", "Ready for your command...", "listening");
    };
    instance.onresult = (event) => {
      if (this.recognition !== instance) return;
      let interim = "";
      let finalTranscript = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        if (event.results[i].isFinal) finalTranscript += event.results[i][0].transcript;
        else interim += event.results[i][0].transcript;
      }
      const activeText = (finalTranscript || interim).trim();
      if (activeText && this.tts.isSpeaking && this.#isLikelyEcho(activeText)) return;
      if (activeText && !this.muted) {
        this.onUserSpeech(activeText);
        if (this.tts.isSpeaking) {
          this.tts.cancel();
          this.onStatus("LISTENING", "Interrupted - listening again.", "interrupted");
        }
      }
      if (finalTranscript.trim()) {
        this.preferences.setLastInputLanguage(this.preferences.detectLanguageHint(finalTranscript));
        this.onFinalTranscript(finalTranscript.trim());
      }
    };
    instance.onerror = (event) => {
      if (this.recognition !== instance) return;
      const permissionError = event.error === "not-allowed" || event.error === "service-not-allowed";
      this.recognitionActive = false;
      this.recognitionStarting = false;
      this.recognition = null;
      if (permissionError) {
        this.microphonePermissionState = "denied";
        this.shouldListen = true;
        this.setPermissionGate(true, "Allow microphone access once; recognition will then start automatically.", true);
        this.onStatus("MICROPHONE ACCESS NEEDED", "Grant microphone permission once; JARVIS will resume automatically.", "error");
        clearTimeout(this.restartTimer);
        this.restartTimer = window.setTimeout(() => this.start(false), HUD_CONFIG.recognitionRetryPermissionMs);
        return;
      }
      if (this.shouldListen) this.#scheduleRestart();
    };
    instance.onend = () => {
      if (this.recognition !== instance) return;
      this.onEnded();
      this.recognitionActive = false;
      this.recognitionStarting = false;
      this.recognition = null;
      if (this.shouldListen) this.#scheduleRestart();
    };
    try {
      instance.start();
    } catch (_) {
      if (this.recognition === instance) {
        this.recognition = null;
        this.recognitionStarting = false;
        this.recognitionActive = false;
      }
      if (this.shouldListen) this.#scheduleRestart();
    }
  }

  #scheduleRestart() {
    clearTimeout(this.restartTimer);
    this.restartAttempts = Math.min(this.restartAttempts + 1, HUD_CONFIG.maxRecognitionRestartAttempts);
    const delay = Math.min(HUD_CONFIG.recognitionRestartBaseMs * (2 ** (this.restartAttempts - 1)), HUD_CONFIG.recognitionRestartMaxMs);
    this.restartTimer = window.setTimeout(() => {
      if (this.shouldListen && !this.recognitionActive && !this.recognitionStarting) this.#setupRecognition();
    }, delay);
  }

  #isLikelyEcho(text) {
    const normalize = (value) => String(value || "").toLowerCase().replace(/[^a-z0-9\u0600-\u06ff\u0900-\u097f]+/g, " ").trim();
    const heard = normalize(text).split(/\s+/).filter(Boolean);
    const spoken = normalize(this.tts.currentText).split(/\s+/).filter(Boolean);
    if (!heard.length || !spoken.length) return false;
    const spokenSet = new Set(spoken);
    const overlap = heard.filter((word) => spokenSet.has(word)).length / heard.length;
    return normalize(text) === normalize(this.tts.currentText) || (heard.length >= 2 && overlap >= 0.65);
  }

  stop() {
    this.shouldListen = false;
    clearTimeout(this.restartTimer);
    if (this.recognition) {
      try { this.recognition.stop(); } catch (_) { /* already stopped */ }
    }
    this.recognition = null;
    this.recognitionActive = false;
    this.recognitionStarting = false;
  }

  destroy() {
    this.stop();
    if (this.microphoneStream) {
      this.microphoneStream.getTracks().forEach((track) => track.stop());
      this.microphoneStream = null;
    }
  }
}
