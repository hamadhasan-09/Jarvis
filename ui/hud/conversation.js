import { HUD_CONFIG } from "./config.js";
import { parseMicrophoneControl, parseVoiceControl, voiceConfirmation } from "./voice-commands.js";

export class ConversationController {
  constructor({ dom, socket, hudState, preferences, tts, voice, lifecycle }) {
    this.dom = dom;
    this.socket = socket;
    this.hudState = hudState;
    this.preferences = preferences;
    this.tts = tts;
    this.voice = voice;
    this.lifecycle = lifecycle;
    this.requestCounter = 0;
    this.speechBuffer = "";
    this.speechFlushTimer = null;
    this.userSpeechPulseTimer = null;
    this.lastSentText = "";
    this.lastSentAt = 0;
    this.pendingRequests = new Map();
    this.expiredRequestIds = new Set();
    this.activeStreamRequestId = null;
    this.activeSpeechRequestId = null;
    this.microphoneMuted = false;
    this.ttsLifecycleUnsubscribers = [
      this.tts.on("end", () => this.#finishSpeechPlayback()),
      this.tts.on("error", (event = {}) => this.cancelActiveReply(event.spokenText || "")),
    ];
  }

  receiveUserSpeech(text) {
    const value = String(text || "").trim();
    if (!value || this.microphoneMuted) return;
    this.dom.userLive.textContent = `YOU: ${value}`;
    this.hudState.setUserSpeaking(true);
    clearTimeout(this.userSpeechPulseTimer);
    this.userSpeechPulseTimer = window.setTimeout(() => this.hudState.setUserSpeaking(false), HUD_CONFIG.userSpeechPulseMs);
  }

  receiveFinalTranscript(text) {
    if (!text) return;
    if (this.microphoneMuted) {
      const micAction = parseMicrophoneControl(text);
      if (micAction === "unmute") this.#handleMicrophoneControl(micAction);
      return;
    }
    this.speechBuffer = [this.speechBuffer, String(text).trim()].filter(Boolean).join(" ").replace(/\s+/g, " ");
    clearTimeout(this.speechFlushTimer);
    this.speechFlushTimer = window.setTimeout(() => this.flushSpeechBuffer(), HUD_CONFIG.speechBufferFlushMs);
  }

  flushSpeechBuffer() {
    clearTimeout(this.speechFlushTimer);
    this.speechFlushTimer = null;
    const normalized = this.speechBuffer.trim().replace(/\s+/g, " ");
    this.speechBuffer = "";
    if (!normalized) return;
    const now = Date.now();
    if (normalized === this.lastSentText && now - this.lastSentAt <= 1500) return;
    this.lastSentText = normalized;
    this.lastSentAt = now;

    const micAction = parseMicrophoneControl(normalized);
    if (micAction) {
      this.#handleMicrophoneControl(micAction);
      return;
    }
    if (this.microphoneMuted) return;

    const voiceControl = parseVoiceControl(normalized);
    if (voiceControl) {
      this.#handleVoiceControl(voiceControl);
      return;
    }

    this.hudState.setBackendState("thinking");
    this.hudState.setTask("THINKING");
    this.hudState.setStatus("PROCESSING", "Understanding your request...");
    this.#sendQuestion(normalized, this.preferences.detectLanguageHint(normalized));
  }

  #handleMicrophoneControl(action) {
    clearTimeout(this.speechFlushTimer);
    this.speechBuffer = "";
    if (action === "mute") {
      this.microphoneMuted = true;
      this.voice.mute();
      this.tts.cancel();
      this.hudState.setUserSpeaking(false);
      this.hudState.setBackendState("idle");
      this.hudState.setStatus("MUTED", "Say “unmute microphone” or “مائیک آن کرو” to resume.");
      return;
    }
    this.microphoneMuted = false;
    this.voice.unmute();
    this.hudState.setBackendState("listening");
    this.hudState.setStatus("LISTENING", "Voice channel resumed.");
  }

  #handleVoiceControl(control) {
    if (control.gender) this.preferences.setGender(control.gender);
    if (control.language) this.preferences.setLanguage(control.language);
    this.preferences.save();
    this.dom.voiceModule.textContent = this.preferences.displayLabel();
    const confirmation = voiceConfirmation(this.preferences, control, this.preferences.lastInputLanguage);
    this.dom.jarvisLive.textContent = confirmation;
    this.tts.speak(confirmation);
    this.voice.restartForPreference();
  }

  #sendQuestion(text, languageHint) {
    const requestId = `${Date.now()}-${++this.requestCounter}`;
    this.activeStreamRequestId = requestId;
    this.dom.jarvisLive.textContent = "";
    const timeoutId = window.setTimeout(() => this.#expireRequest(requestId), HUD_CONFIG.streamTimeoutMs);
    this.pendingRequests.set(requestId, timeoutId);
    this.socket.emit("user_speech", { text, request_id: requestId, language_hint: languageHint || this.preferences.detectLanguageHint(text) });
  }

  cancelActiveReply(heardText = "") {
    const requestId = this.activeStreamRequestId || this.activeSpeechRequestId;
    if (!requestId) return false;
    this.socket.emit("voice_cancel", { request_id: requestId, heard_text: String(heardText || "") });
    this.activeStreamRequestId = null;
    this.activeSpeechRequestId = null;
    this.expiredRequestIds.add(requestId);
    window.setTimeout(() => this.expiredRequestIds.delete(requestId), HUD_CONFIG.lateEventQuarantineMs);
    this.dom.jarvisLive.textContent = String(heardText || "");
    return true;
  }

  #finishSpeechPlayback() {
    const requestId = this.activeSpeechRequestId;
    if (!requestId) return;
    this.socket.emit("voice_playback_done", { request_id: requestId });
    this.activeSpeechRequestId = null;
  }

  #expireRequest(requestId) {
    if (!this.pendingRequests.has(requestId)) return;
    window.clearTimeout(this.pendingRequests.get(requestId));
    this.pendingRequests.delete(requestId);
    this.expiredRequestIds.add(requestId);
    window.setTimeout(() => this.expiredRequestIds.delete(requestId), HUD_CONFIG.lateEventQuarantineMs);
    if (this.activeStreamRequestId === requestId) {
      this.activeStreamRequestId = null;
      this.dom.jarvisLive.textContent = "";
      this.dom.taskState.textContent = "IDLE";
      this.hudState.setBackendState("idle");
      this.hudState.setStatus("LISTENING", "No response received. Ready for your next command.");
    }
  }

  acknowledgeRequest(requestId) {
    if (requestId && this.pendingRequests.has(requestId)) {
      window.clearTimeout(this.pendingRequests.get(requestId));
      this.pendingRequests.delete(requestId);
      return;
    }
    if (!requestId && this.pendingRequests.size) {
      const first = this.pendingRequests.keys().next().value;
      window.clearTimeout(this.pendingRequests.get(first));
      this.pendingRequests.delete(first);
    }
  }

  acceptsEvent(requestId) {
    const id = requestId ? String(requestId) : null;
    if (id && this.expiredRequestIds.has(id)) return false;
    if (id && this.activeStreamRequestId && id !== this.activeStreamRequestId) return false;
    return true;
  }

  handleToken(data = {}) {
    const requestId = data.request_id ? String(data.request_id) : null;
    if (!this.acceptsEvent(requestId)) return;
    if (data.reset) {
      this.activeStreamRequestId = requestId || this.activeStreamRequestId;
      this.dom.jarvisLive.textContent = "";
      return;
    }
    if (data.is_first) {
      this.activeStreamRequestId = requestId || this.activeStreamRequestId;
      this.dom.jarvisLive.textContent = data.token || "";
      this.dom.taskState.textContent = "GENERATING";
      this.hudState.setBackendState("generating");
      this.hudState.setStatus("THINKING", "Streaming response...");
      return;
    }
    this.dom.jarvisLive.textContent += data.token || "";
  }

  handleReply(data = {}) {
    const requestId = data.request_id ? String(data.request_id) : null;
    if (requestId && this.expiredRequestIds.has(requestId)) return false;
    this.acknowledgeRequest(requestId);
    if (this.activeStreamRequestId && requestId && requestId !== this.activeStreamRequestId) return false;
    this.activeStreamRequestId = null;
    const text = String(data.text || "");
    this.dom.jarvisLive.textContent = text;
    this.dom.taskState.textContent = "IDLE";
    this.hudState.setBackendState("idle");
    if (!HUD_CONFIG.localVoiceMode && text) {
      this.activeSpeechRequestId = requestId;
      if (!this.tts.speak(text)) this.#finishSpeechPlayback();
    } else if (requestId && !HUD_CONFIG.localVoiceMode) {
      this.activeSpeechRequestId = requestId;
      this.#finishSpeechPlayback();
    }
    return true;
  }

  destroy() {
    clearTimeout(this.speechFlushTimer);
    clearTimeout(this.userSpeechPulseTimer);
    for (const timeoutId of this.pendingRequests.values()) window.clearTimeout(timeoutId);
    this.pendingRequests.clear();
    this.expiredRequestIds.clear();
    this.activeSpeechRequestId = null;
    for (const unsubscribe of this.ttsLifecycleUnsubscribers) unsubscribe();
    this.speechBuffer = "";
  }
}
