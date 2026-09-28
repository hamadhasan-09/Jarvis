export class ApprovalController {
  constructor({ dom, socket, hudState, accessibility, tts, lifecycle }) {
    this.dom = dom;
    this.socket = socket;
    this.hudState = hudState;
    this.accessibility = accessibility;
    this.tts = tts;
    this.stateTimeoutId = null;
    this.currentId = null;
    this.currentTaskId = null;
    lifecycle.listen(dom.approvalApproveButton, "click", () => this.respond(true));
    lifecycle.listen(dom.approvalDenyButton, "click", () => this.respond(false));
    lifecycle.listen(dom.approvalCancelButton, "click", () => this.respond(false));
  }

  show(data = {}) {
    this.currentId = data.id || null;
    this.currentTaskId = data.task_id || null;
    this.dom.approvalAction.textContent = data.action_type || "Unknown Action";
    const risk = String(data.risk_level || "unknown").toLowerCase();
    this.dom.approvalRisk.textContent = risk.toUpperCase();
    this.dom.approvalRisk.style.color = risk === "high" || risk === "destructive" ? "var(--danger)" : (risk === "low" ? "var(--green)" : "var(--amber)");
    this.dom.approvalTarget.textContent = data.target || "N/A";
    this.dom.approvalPrompt.textContent = data.prompt_text || "JARVIS requires authorization to proceed with this action.";
    this.dom.approvalModal.style.display = "block";
    this.accessibility.setModalOpen(true, this.dom.approvalApproveButton);
    this.hudState.setBackendState("waiting_confirmation");
    this.hudState.setTask("AUTHORIZATION");
    this.hudState.setStatus("AUTHORIZATION", "User approval required.");
    this.tts.speak("Authorization required. Please review the pending action, Sir.");
  }

  close() {
    this.dom.approvalModal.style.display = "none";
    this.accessibility.setModalOpen(false);
    this.currentId = null;
    this.currentTaskId = null;
  }

  respond(result) {
    if (typeof result !== "boolean" || !this.currentId) return false;
    this.socket.emit("approval_response", { id: this.currentId, task_id: this.currentTaskId, result });
    this.close();
    clearTimeout(this.stateTimeoutId);
    if (result) {
      this.hudState.setBackendState("approved");
      this.hudState.setTask("APPROVED");
      this.hudState.setStatus("APPROVED", "Authorization confirmed.");
    } else {
      this.hudState.setBackendState("cancelled");
      this.hudState.setTask("CANCELLED");
      this.hudState.setStatus("DENIED", "Authorization rejected by user.");
      this.stateTimeoutId = window.setTimeout(() => {
        this.hudState.setBackendState("idle");
        this.hudState.setTask("IDLE");
        this.hudState.setStatus("STANDBY", "Ready for your command...");
      }, 3000);
    }
    return true;
  }

  destroy() {
    clearTimeout(this.stateTimeoutId);
    this.close();
  }
}
