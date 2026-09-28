import { StateController } from "../core/StateController.js";

export class HudState {
  constructor({ dom, renderer }) {
    this.dom = dom;
    this.renderer = renderer;
    this.controller = new StateController();
    this.backendState = "idle";
    this.unsubscribe = this.controller.onChange((coreState) => {
      this.renderer.setState(coreState, this.backendState);
    });
    this.renderer.setState(this.controller.getState(), this.backendState);
  }

  setBackendState(state) {
    this.backendState = String(state || "idle").toLowerCase();
    this.controller.setBackendState(this.backendState);
    this.renderer.setState(this.controller.getState(), this.backendState);
  }

  setUserSpeaking(active) {
    this.controller.setUserSpeaking(active);
    this.renderer.setUserSpeaking(active);
    this.renderer.setState(this.controller.getState(), this.backendState);
  }

  setJarvisSpeaking(active) {
    this.controller.setJarvisSpeaking(active);
    this.renderer.setState(this.controller.getState(), this.backendState);
  }

  notifyInterrupted() {
    this.controller.notifyInterrupted();
    this.renderer.setState(this.controller.getState(), this.backendState);
  }

  setStatus(label, detail = "") {
    this.dom.statusLabel.textContent = String(label || "");
    this.dom.statusDetail.textContent = String(detail || "");
  }

  setTask(label) {
    this.dom.taskState.textContent = String(label || "");
  }

  getCoreState() {
    return this.controller.getState();
  }

  destroy() {
    if (this.unsubscribe) this.unsubscribe();
    this.renderer.destroy();
  }
}
