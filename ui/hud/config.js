export const HUD_CONFIG = Object.freeze({
  localVoiceMode: true,
  socket: Object.freeze({
    reconnection: true,
    reconnectionAttempts: Infinity,
    reconnectionDelay: 500,
    timeout: 10000,
  }),
  streamTimeoutMs: 40000,
  lateEventQuarantineMs: 60000,
  speechBufferFlushMs: 850,
  userSpeechPulseMs: 680,
  terminalStateResetMs: 3000,
  recognitionRestartBaseMs: 500,
  recognitionRestartMaxMs: 5000,
  recognitionRetryPermissionMs: 12000,
  maxRecognitionRestartAttempts: 6,
  // The current reference-image renderer does not consume analyser values yet.
  // Keep the controller interface ready without changing the current visuals.
  audioReactiveEnabled: false,
});

export const VOICE_STORAGE_KEYS = Object.freeze({
  gender: "nexus.voiceGender",
  language: "nexus.voiceLanguage",
});
