export class SocketEventBridge {
  constructor(options) {
    if (typeof globalThis.io !== "function") {
      throw new Error("Socket.IO client is not available");
    }
    this.socket = globalThis.io(options);
    this.unsubscribers = new Set();
  }

  on(eventName, handler) {
    this.socket.on(eventName, handler);
    const unsubscribe = () => {
      this.socket.off(eventName, handler);
      this.unsubscribers.delete(unsubscribe);
    };
    this.unsubscribers.add(unsubscribe);
    return unsubscribe;
  }

  emit(eventName, payload) {
    this.socket.emit(eventName, payload);
  }

  destroy() {
    for (const unsubscribe of [...this.unsubscribers]) unsubscribe();
    this.unsubscribers.clear();
    this.socket.disconnect();
  }
}
