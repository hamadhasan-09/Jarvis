import { createJarvisCore } from "../core/createJarvisCore.js";

export class ProceduralCoreRenderer {
  constructor({ canvas }) {
    if (!canvas) {
      throw new Error(
        "JARVIS core canvas was not found.",
      );
    }

    this.core = createJarvisCore(canvas);
  }

  setState(_coreState, backendState = "idle") {
    this.core.setBackendState(
      backendState || "idle",
    );
  }

  setUserSpeaking(active) {
    this.core.setUserSpeaking(
      Boolean(active),
    );
  }

  setJarvisSpeaking(active) {
    this.core.setJarvisSpeaking(
      Boolean(active),
    );
  }

  notifyInterrupted() {
    this.core.notifyInterrupted();
  }

  async attachMicrophone(stream) {
    return this.core.attachMicrophone(
      stream,
    );
  }

  detachMicrophone() {
    this.core.detachMicrophone?.();
  }

  setAudioLevels(levels) {
    if (!levels) {
      this.core.setTtsEnergy(null);
      return;
    }

    const rms = Number(levels.rms);

    if (Number.isFinite(rms)) {
      this.core.setTtsEnergy(
        Math.max(
          0,
          Math.min(1, rms),
        ),
      );
    } else {
      this.core.setTtsEnergy(null);
    }
  }

  destroy() {
    this.core.destroy();
  }
}