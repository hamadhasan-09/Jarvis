import { STATE_POSE } from "./visualSpec.js";
import { CORE_STATES, mapToCoreState } from "./states.js";

export class StateController {
  constructor() {
    this.backendState = "idle";
    this.flags = {
      userSpeaking: false,
      jarvisSpeaking: false,
      interrupted: false,
      error: false,
    };
    this.current = CORE_STATES.IDLE;
    this.listeners = new Set();
  }

  onChange(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  getState() {
    return this.current;
  }

  getPose() {
    return STATE_POSE[this.current];
  }

  setBackendState(backendState) {
    this.backendState = backendState;
    if (backendState === "error" || backendState === "failed") this.flags.error = true;
    else this.flags.error = false;
    this.#commit();
  }

  setUserSpeaking(active) {
    this.flags.userSpeaking = Boolean(active);
    if (active) this.flags.interrupted = false;
    this.#commit();
  }

  setJarvisSpeaking(active) {
    this.flags.jarvisSpeaking = Boolean(active);
    if (active) this.flags.interrupted = false;
    this.#commit();
  }

  notifyInterrupted() {
    this.flags.interrupted = true;
    this.flags.jarvisSpeaking = false;
    this.#commit();
    globalThis.setTimeout(() => {
      this.flags.interrupted = false;
      this.#commit();
    }, 280);
  }

  #commit() {
    const next = mapToCoreState(this.backendState, this.flags);
    if (next === this.current) return;
    this.current = next;
    for (const listener of this.listeners) listener(next, this.getPose());
  }
}
