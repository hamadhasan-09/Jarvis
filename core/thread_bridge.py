"""Thread-Safe GUI Bridge for Tkinter"""
import queue


class ThreadBridge:
    def __init__(self, tk_root):
        self.root = tk_root
        self.queue = queue.Queue()
        self._running = True
        self._poll()

    def _poll(self):
        try:
            while True:
                func, args, kwargs = self.queue.get_nowait()
                try:
                    func(*args, **kwargs)
                except Exception as e:
                    print(f"Bridge error: {e}")
        except queue.Empty:
            pass
        if self._running:
            self.root.after(50, self._poll)

    def call(self, func, *args, **kwargs):
        self.queue.put((func, args, kwargs))

    def stop(self):
        self._running = False