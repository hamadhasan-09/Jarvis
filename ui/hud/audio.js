export class AudioInterface {
  constructor({
    renderer,
    enabled = true,
  } = {}) {
    this.renderer = renderer;
    this.enabled = Boolean(enabled);
    this.microphoneStream = null;
  }

  async attachMicrophone(stream) {
    if (!this.enabled || !this.renderer) {
      return false;
    }

    this.microphoneStream = stream;

    if (
      typeof this.renderer.attachMicrophone ===
      "function"
    ) {
      return Boolean(
        await this.renderer.attachMicrophone(
          stream,
        ),
      );
    }

    return false;
  }

  setTtsEnergy(rms) {
    if (!this.enabled || !this.renderer) {
      return;
    }

    if (
      typeof this.renderer.setAudioLevels ===
      "function"
    ) {
      this.renderer.setAudioLevels({
        rms,
        external: true,
      });
    }
  }

  detachMicrophone() {
    if (
      this.renderer &&
      typeof this.renderer.detachMicrophone ===
        "function"
    ) {
      this.renderer.detachMicrophone();
    }

    this.microphoneStream = null;
  }

  destroy() {
    this.detachMicrophone();
  }
}