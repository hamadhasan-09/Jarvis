export class AccessibilityController {
  constructor({ modal, focusTarget }) {
    this.modal = modal;
    this.focusTarget = focusTarget || null;
    this.previousFocus = null;
  }

  setModalOpen(open, focusTarget = this.focusTarget) {
    if (!this.modal) return;
    if (open) {
      this.previousFocus = document.activeElement;
      this.modal.setAttribute("aria-hidden", "false");
      const target = focusTarget || this.modal.querySelector("button");
      if (target && typeof target.focus === "function") {
        queueMicrotask(() => target.focus({ preventScroll: true }));
      }
      return;
    }

    this.modal.setAttribute("aria-hidden", "true");
    const restore = this.previousFocus;
    this.previousFocus = null;
    if (restore && typeof restore.focus === "function") {
      queueMicrotask(() => restore.focus({ preventScroll: true }));
    }
  }

  destroy() {
    this.setModalOpen(false);
  }
}
