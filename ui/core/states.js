export const CORE_STATES = Object.freeze({
  IDLE: "IDLE",
  LISTENING: "LISTENING",
  USER_SPEAKING: "USER_SPEAKING",
  THINKING: "THINKING",
  JARVIS_SPEAKING: "JARVIS_SPEAKING",
  INTERRUPTED: "INTERRUPTED",
  ERROR: "ERROR",
});

const BACKEND_TO_CORE = Object.freeze({
  idle: CORE_STATES.IDLE,
  listening: CORE_STATES.LISTENING,
  thinking: CORE_STATES.THINKING,
  generating: CORE_STATES.THINKING,
  retrieving: CORE_STATES.THINKING,
  searching: CORE_STATES.THINKING,
  verifying: CORE_STATES.THINKING,
  executing: CORE_STATES.THINKING,
  waiting_confirmation: CORE_STATES.THINKING,
  approved: CORE_STATES.THINKING,
  completed: CORE_STATES.IDLE,
  cancelled: CORE_STATES.IDLE,
  speaking: CORE_STATES.JARVIS_SPEAKING,
  error: CORE_STATES.ERROR,
  failed: CORE_STATES.ERROR,
});

/**
 * Pure mapping from HUD/backend labels to renderer states.
 * Local voice flags outrank backend labels for presence.
 */
export function mapToCoreState(backendState, flags = {}) {
  if (flags.error) return CORE_STATES.ERROR;
  if (flags.interrupted) return CORE_STATES.INTERRUPTED;
  if (flags.jarvisSpeaking) return CORE_STATES.JARVIS_SPEAKING;
  if (flags.userSpeaking) return CORE_STATES.USER_SPEAKING;
  const normalized = String(backendState || "idle").toLowerCase();
  if (normalized === "generating") return CORE_STATES.THINKING;
  return BACKEND_TO_CORE[normalized] || CORE_STATES.IDLE;
}
