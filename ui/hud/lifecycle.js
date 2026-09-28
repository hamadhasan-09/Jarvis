export class LifecycleManager {
  constructor() {
    this.cleanups = new Set();
    this.destroyed = false;
  }

  add(cleanup) {
    if (typeof cleanup !== "function") return cleanup;
    if (this.destroyed) {
      cleanup();
      return cleanup;
    }
    this.cleanups.add(cleanup);
    return cleanup;
  }

  listen(target, eventName, handler, options) {
    target.addEventListener(eventName, handler, options);
    this.add(() => target.removeEventListener(eventName, handler, options));
    return handler;
  }

  destroy() {
    if (this.destroyed) return;
    this.destroyed = true;
    for (const cleanup of [...this.cleanups].reverse()) {
      try { cleanup(); } catch (_) { /* cleanup is best effort */ }
    }
    this.cleanups.clear();
  }
}
