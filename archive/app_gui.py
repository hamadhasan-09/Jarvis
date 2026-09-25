"""
JARVIS v6.0 - Redesigned Desktop UI
Modern HUD-style interface with chat bubbles, animations, settings panel
"""

import sys
import os
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, simpledialog
from datetime import datetime

os.environ["PYTHONIOENCODING"] = "utf-8"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

from voice_engine import VoiceEngine
from core.error_handler import ErrorHandler
from core.memory_manager import PersistentMemory
from core.rate_limiter import RateLimiter
from core.thread_bridge import ThreadBridge
from security_v2.key_manager import SecureKeyManager
from security_v2.prompt_defense import PromptDefense
from security_v2.sandbox import CommandSandbox
from modules.human_approval import ApprovalGateway
from models.model_registry import ModelRegistry
from models.model_router import ModelRouter
from engine.planner import Planner
from engine.executor import Executor
from engine.verifier import Verifier
from engine.learner import Learner
from engine.orchestrator import Orchestrator


# ============================================
# THEME COLORS
# ============================================
BG_DARK = "#0a0e1a"
BG_PANEL = "#111827"
BG_CARD = "#1e293b"
BG_INPUT = "#0f172a"
CYAN = "#00e5ff"
CYAN_DIM = "#0891b2"
GREEN = "#10b981"
GREEN_DIM = "#065f46"
BLUE = "#3b82f6"
ORANGE = "#f59e0b"
RED = "#ef4444"
PINK = "#ec4899"
WHITE = "#f1f5f9"
GRAY = "#64748b"
GRAY_DIM = "#334155"
USER_BUBBLE = "#1e3a5f"
JARVIS_BUBBLE = "#0f2a1f"
SYSTEM_BUBBLE = "#1a1a2e"


class JarvisGUI:
    """Modern Desktop GUI for JARVIS / Nexus."""

    def __init__(self, root):
        self.root = root
        self.root.title("J.A.R.V.I.S. v6.0")
        self.root.geometry("950x750")
        self.root.minsize(800, 600)
        self.root.configure(bg=BG_DARK)

        # Try to set icon (optional)
        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        # Initialize subsystems
        self._init_subsystems()

        # Thread-safe GUI bridge
        self.bridge = ThreadBridge(self.root)

        # State
        self._is_listening = False
        self._is_thinking = False
        self._pulse_active = False
        self._msg_count = 0

        # Build UI
        self._setup_styles()
        self._build_ui()
        self._bind_shortcuts()

        # Startup
        self.root.after(500, self._startup_sequence)

    def _init_subsystems(self):
        """Initialize all JARVIS subsystems."""
        self.voice = VoiceEngine()
        self.memory = PersistentMemory()
        self.keys = SecureKeyManager()
        self.registry = ModelRegistry(self.keys)
        self.router = ModelRouter(self.registry)
        self.errors = ErrorHandler()
        self.rate_limiter = RateLimiter()
        self.prompt_defense = PromptDefense()
        self.gateway = ApprovalGateway(gui_root=self.root)
        self.sandbox = CommandSandbox()
        self.planner = Planner(self.router)
        self.executor = Executor(self.registry, self.gateway)
        self.verifier = Verifier(self.router)
        self.learner = Learner()
        self.orchestrator = Orchestrator(
            self.planner, self.router, self.executor,
            self.verifier, self.learner, self.memory,
            self.gateway, self.errors
        )

    def _setup_styles(self):
        """Configure ttk styles for modern look."""
        style = ttk.Style()
        style.theme_use("clam")

        style.configure("Dark.TFrame", background=BG_PANEL)
        style.configure("Card.TFrame", background=BG_CARD)
        style.configure("Dark.TLabel", background=BG_PANEL, foreground=WHITE,
                         font=("Segoe UI", 10))
        style.configure("Title.TLabel", background=BG_PANEL, foreground=CYAN,
                         font=("Segoe UI", 18, "bold"))
        style.configure("Status.TLabel", background=BG_PANEL, foreground=GREEN,
                         font=("Segoe UI", 10, "bold"))
        style.configure("Timestamp.TLabel", background=BG_DARK, foreground=GRAY,
                         font=("Segoe UI", 8))

        style.configure("Accent.TButton", background=CYAN, foreground=BG_DARK,
                         font=("Segoe UI", 10, "bold"), padding=8)
        style.map("Accent.TButton",
                   background=[("active", CYAN_DIM), ("pressed", CYAN)])

        style.configure("Danger.TButton", background=RED, foreground=WHITE,
                         font=("Segoe UI", 9), padding=5)
        style.map("Danger.TButton",
                   background=[("active", "#dc2626")])

        style.configure("Ghost.TButton", background=BG_CARD, foreground=WHITE,
                         font=("Segoe UI", 9), padding=5)
        style.map("Ghost.TButton",
                   background=[("active", GRAY_DIM)])

        style.configure("Dark.TCombobox", fieldbackground=BG_INPUT,
                         background=BG_CARD, foreground=WHITE)

    def _build_ui(self):
        """Build the complete UI layout."""

        # === TOP HEADER BAR ===
        header = tk.Frame(self.root, bg=BG_PANEL, height=65)
        header.pack(fill=tk.X, side=tk.TOP)
        header.pack_propagate(False)

        # Left: Logo + Title
        logo_frame = tk.Frame(header, bg=BG_PANEL)
        logo_frame.pack(side=tk.LEFT, padx=20, pady=10)

        tk.Label(logo_frame, text="⚡", font=("Segoe UI", 20),
                 fg=CYAN, bg=BG_PANEL).pack(side=tk.LEFT)
        tk.Label(logo_frame, text=" NEXUS", font=("Segoe UI", 16, "bold"),
                 fg=WHITE, bg=BG_PANEL).pack(side=tk.LEFT)
        tk.Label(logo_frame, text="  v6.0", font=("Segoe UI", 9),
                 fg=GRAY, bg=BG_PANEL).pack(side=tk.LEFT)

        # Center: Status indicator
        self.status_frame = tk.Frame(header, bg=BG_PANEL)
        self.status_frame.pack(side=tk.LEFT, padx=40)

        self.status_dot = tk.Label(self.status_frame, text="●",
                                    font=("Segoe UI", 12), fg=GREEN, bg=BG_PANEL)
        self.status_dot.pack(side=tk.LEFT)
        self.status_lbl = tk.Label(self.status_frame, text=" SYSTEM ONLINE",
                                    font=("Segoe UI", 10, "bold"),
                                    fg=GREEN, bg=BG_PANEL)
        self.status_lbl.pack(side=tk.LEFT)

        # Right: Quick buttons
        btn_frame = tk.Frame(header, bg=BG_PANEL)
        btn_frame.pack(side=tk.RIGHT, padx=15, pady=12)

        tk.Button(btn_frame, text="⚙️ Settings", font=("Segoe UI", 9),
                  bg=BG_CARD, fg=WHITE, activebackground=GRAY_DIM,
                  activeforeground=WHITE, relief=tk.FLAT, padx=8,
                  command=self._open_settings).pack(side=tk.LEFT, padx=3)

        tk.Button(btn_frame, text="🧠 Memory", font=("Segoe UI", 9),
                  bg=BG_CARD, fg=WHITE, activebackground=GRAY_DIM,
                  activeforeground=WHITE, relief=tk.FLAT, padx=8,
                  command=self._show_memory_stats).pack(side=tk.LEFT, padx=3)

        tk.Button(btn_frame, text="📊 Models", font=("Segoe UI", 9),
                  bg=BG_CARD, fg=WHITE, activebackground=GRAY_DIM,
                  activeforeground=WHITE, relief=tk.FLAT, padx=8,
                  command=self._show_model_stats).pack(side=tk.LEFT, padx=3)

        # === TOOLBAR ===
        toolbar = tk.Frame(self.root, bg=BG_CARD, height=45)
        toolbar.pack(fill=tk.X, padx=10, pady=(5, 2))
        toolbar.pack_propagate(False)

        tk.Label(toolbar, text="🎙️ Voice:", font=("Segoe UI", 9, "bold"),
                 fg=GRAY, bg=BG_CARD).pack(side=tk.LEFT, padx=(15, 5))

        self.voice_var = tk.StringVar(value="english_male")
        voice_names = {
            "english_male": "🇺🇸 English Male",
            "english_female": "🇺🇸 English Female",
            "english_british": "🇬🇧 British Male",
            "urdu_male": "🇵🇰 Urdu Male",
            "urdu_female": "🇵🇰 Urdu Female",
            "hindi_male": "🇮🇳 Hindi Male",
            "hindi_female": "🇮🇳 Hindi Female",
        }
        self.voice_display = [voice_names[k] for k in self.voice.VOICES.keys()]
        self.voice_keys = list(self.voice.VOICES.keys())

        self.voice_combo = ttk.Combobox(
            toolbar, values=self.voice_display,
            state="readonly", width=20, font=("Segoe UI", 9)
        )
        self.voice_combo.current(0)
        self.voice_combo.pack(side=tk.LEFT, padx=5)
        self.voice_combo.bind("<<ComboboxSelected>>", self._on_voice_change)

        tk.Button(toolbar, text="🔄 Swap Gender", font=("Segoe UI", 8),
                  bg=GRAY_DIM, fg=WHITE, activebackground=GRAY,
                  activeforeground=WHITE, relief=tk.FLAT, padx=6,
                  command=self._toggle_gender).pack(side=tk.LEFT, padx=5)

        tk.Button(toolbar, text="⏹️ Stop", font=("Segoe UI", 8),
                  bg=RED, fg=WHITE, activebackground="#dc2626",
                  activeforeground=WHITE, relief=tk.FLAT, padx=6,
                  command=self._stop_voice).pack(side=tk.RIGHT, padx=15)

        # === CHAT AREA ===
        chat_outer = tk.Frame(self.root, bg=BG_DARK)
        chat_outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.chat_canvas = tk.Canvas(chat_outer, bg=BG_DARK,
                                      highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(chat_outer, orient=tk.VERTICAL,
                                   command=self.chat_canvas.yview)
        self.chat_inner = tk.Frame(self.chat_canvas, bg=BG_DARK)

        self.chat_inner.bind(
            "<Configure>",
            lambda e: self.chat_canvas.configure(scrollregion=self.chat_canvas.bbox("all"))
        )
        self.chat_canvas.create_window((0, 0), window=self.chat_inner, anchor="nw")
        self.chat_canvas.configure(yscrollcommand=scrollbar.set)

        self.chat_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Mouse wheel scrolling
        self.chat_canvas.bind_all("<MouseWheel>",
            lambda e: self.chat_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        # === INPUT BAR ===
        input_bar = tk.Frame(self.root, bg=BG_PANEL, height=70)
        input_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=10, pady=(2, 10))
        input_bar.pack_propagate(False)

        # Mic button
        self.btn_mic = tk.Button(
            input_bar, text="🎤", font=("Segoe UI", 18),
            bg=BLUE, fg=WHITE, activebackground="#2563eb",
            activeforeground=WHITE, relief=tk.FLAT,
            width=3, height=1, command=self._start_voice_input
        )
        self.btn_mic.pack(side=tk.LEFT, padx=(15, 8), pady=10)

        # Text input
        self.entry = tk.Entry(
            input_bar, font=("Segoe UI", 13),
            bg=BG_INPUT, fg=WHITE, insertbackground=CYAN,
            relief=tk.FLAT, bd=12
        )
        self.entry.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=12)
        self.entry.bind("<Return>", lambda e: self._send_text())
        self.entry.bind("<Shift-Return>", lambda e: None)

        # Placeholder text
        self._placeholder = "Type a message or press Enter on empty for voice..."
        self.entry.insert(0, self._placeholder)
        self.entry.config(fg=GRAY)
        self.entry.bind("<FocusIn>", self._on_entry_focus_in)
        self.entry.bind("<FocusOut>", self._on_entry_focus_out)

        # Send button
        self.btn_send = tk.Button(
            input_bar, text="➤", font=("Segoe UI", 16, "bold"),
            bg=CYAN, fg=BG_DARK, activebackground=CYAN_DIM,
            activeforeground=BG_DARK, relief=tk.FLAT,
            width=3, height=1, command=self._send_text
        )
        self.btn_send.pack(side=tk.RIGHT, padx=(8, 15), pady=10)

    def _bind_shortcuts(self):
        """Bind keyboard shortcuts."""
        self.root.bind("<Control-m>", lambda e: self._start_voice_input())
        self.root.bind("<Control-q>", lambda e: self._shutdown())
        self.root.bind("<Control-s>", lambda e: self._open_settings())
        self.root.bind("<Escape>", lambda e: self._stop_voice())

    # ============================================
    # CHAT BUBBLE SYSTEM
    # ============================================

    def _add_bubble(self, speaker, message, bubble_type="jarvis"):
        """Add a chat bubble to the conversation."""
        self._msg_count += 1
        timestamp = datetime.now().strftime("%I:%M %p")

        # Container frame for the bubble row
        row = tk.Frame(self.chat_inner, bg=BG_DARK)
        row.pack(fill=tk.X, padx=15, pady=4, anchor="w" if bubble_type != "user" else "e")

        # Color scheme
        colors = {
            "user": (USER_BUBBLE, CYAN, "YOU"),
            "jarvis": (JARVIS_BUBBLE, GREEN, "JARVIS"),
            "system": (SYSTEM_BUBBLE, GRAY, "SYSTEM"),
            "error": ("#2d1b1b", RED, "ERROR"),
        }
        bg, accent, label = colors.get(bubble_type, colors["system"])

        # Speaker label + timestamp
        header_text = f"{label}  •  {timestamp}"
        tk.Label(row, text=header_text, font=("Segoe UI", 8, "bold"),
                 fg=accent, bg=BG_DARK).pack(anchor="w", padx=5)

        # Message bubble
        bubble = tk.Frame(row, bg=bg, padx=12, pady=8)
        bubble.pack(anchor="w" if bubble_type != "user" else "e", padx=5)

        tk.Label(bubble, text=message, font=("Segoe UI", 11),
                 fg=WHITE, bg=bg, wraplength=650, justify="left").pack()

        # Auto-scroll to bottom
        self.root.after(100, lambda: self.chat_canvas.yview_moveto(1.0))

    def _append_chat(self, speaker, message, tag):
        """Thread-safe chat update using bubble system."""
        bubble_map = {
            "user": "user",
            "jarvis": "jarvis",
            "system": "system",
        }
        btype = bubble_map.get(tag, "system")

        def _update():
            self._add_bubble(speaker, message, btype)

        if threading.current_thread() is threading.main_thread():
            _update()
        else:
            self.bridge.call(_update)

    # ============================================
    # STATUS & ANIMATIONS
    # ============================================

    def _set_status(self, text, color=GREEN):
        """Thread-safe status update with pulsing dot."""
        def _update():
            self.status_dot.config(fg=color)
            self.status_lbl.config(text=f" {text}", fg=color)

        if threading.current_thread() is threading.main_thread():
            _update()
        else:
            self.bridge.call(_update)

    def _pulse_status(self, color, active=True):
        """Animate the status dot with a pulse effect."""
        self._pulse_active = active
        if not active:
            return

        def _do_pulse():
            if not self._pulse_active:
                return
            current = self.status_dot.cget("fg")
            new_color = BG_DARK if current == color else color
            self.status_dot.config(fg=new_color)
            self.root.after(500, _do_pulse)

        _do_pulse()

    # ============================================
    # STARTUP SEQUENCE
    # ============================================

    def _startup_sequence(self):
        """Animated startup greeting."""
        self._add_bubble("SYSTEM", "Initializing JARVIS v6.0...", "system")

        self.root.after(800, lambda: self._add_bubble(
            "SYSTEM", "All subsystems online. Voice engine active.", "system"))

        self.root.after(1600, lambda: self._add_bubble(
            "SYSTEM", "Security layer armed. Memory loaded.", "system"))

        self.root.after(2400, self._greet_user)

    def _greet_user(self):
        """Contextual greeting."""
        hour = datetime.now().hour
        user = os.getenv("USER_NAME", "Sir")

        if hour < 12:
            greet = f"Good morning, {user}. All systems are online and ready."
        elif hour < 17:
            greet = f"Good afternoon, {user}. Standing by for your instructions."
        else:
            greet = f"Good evening, {user}. Systems are fully operational."

        self._add_bubble("JARVIS", greet, "jarvis")
        self.voice.speak(greet)

    # ============================================
    # INPUT HANDLING
    # ============================================

    def _on_entry_focus_in(self, event):
        if self.entry.get() == self._placeholder:
            self.entry.delete(0, tk.END)
            self.entry.config(fg=WHITE)

    def _on_entry_focus_out(self, event):
        if not self.entry.get().strip():
            self.entry.insert(0, self._placeholder)
            self.entry.config(fg=GRAY)

    def _send_text(self):
        text = self.entry.get().strip()
        if not text or text == self._placeholder:
            return

        self.voice.stop_speaking()
        self.entry.delete(0, tk.END)
        self._on_entry_focus_out(None)

        self._add_bubble("YOU", text, "user")
        threading.Thread(target=self._process_message, args=(text,), daemon=True).start()

    def _start_voice_input(self):
        if self._is_listening:
            return

        def _listen_worker():
            self._is_listening = True
            self.voice.stop_speaking()
            self._set_status("LISTENING...", ORANGE)
            self._pulse_status(ORANGE)
            self.bridge.call(lambda: self.btn_mic.config(bg=RED, text="🔴"))

            user_text = self.voice.listen(max_duration=15)

            self.bridge.call(lambda: self.btn_mic.config(bg=BLUE, text="🎤"))
            self._pulse_status(GREEN, active=False)
            self._is_listening = False

            if user_text:
                self._add_bubble("YOU (Voice)", user_text, "user")
                self._process_message(user_text)
            else:
                self._set_status("SYSTEM ONLINE", GREEN)

        threading.Thread(target=_listen_worker, daemon=True).start()

    # ============================================
    # MESSAGE PROCESSING
    # ============================================

    def _process_message(self, user_input):
        self._set_status("THINKING...", BLUE)
        self._pulse_status(BLUE)

        defense = self.prompt_defense.analyze(user_input)
        if not defense["is_safe"]:
            user_input = self.prompt_defense.sanitize(user_input)

        system_instruction = (
            "You are JARVIS, an autonomous AI personal assistant. "
            "If the user speaks in Urdu or Roman Urdu, reply fluently in Urdu. "
            "If in Hindi, reply in Hindi. If in English, reply in English. "
            "Be brilliant, natural, and concise."
        )

        try:
            choice = self.router.select("fast_query")
            context = self.memory.get_context(user_input)
            if context:
                system_instruction += f"\n\nContext:\n{context}"

            response = self.registry.generate(
                choice["model"], user_input, system_instruction,
                max_tokens=1500, temperature=0.7
            )

            self._add_bubble("JARVIS", response, "jarvis")
            self._set_status("SPEAKING...", CYAN)
            self.voice.speak(response)

            self.memory.save_message("chat", "user", user_input)
            self.memory.save_message("chat", "assistant", response)

            if "remember" in user_input.lower() or "یاد رکھنا" in user_input:
                self.memory.save_fact("user_pref", user_input)

        except Exception as e:
            self._add_bubble("ERROR", str(e)[:200], "error")
        finally:
            self._pulse_status(GREEN, active=False)
            self._set_status("SYSTEM ONLINE", GREEN)

    # ============================================
    # VOICE CONTROLS
    # ============================================

    def _on_voice_change(self, event=None):
        idx = self.voice_combo.current()
        if 0 <= idx < len(self.voice_keys):
            key = self.voice_keys[idx]
            self.voice.set_voice(key)
            self._add_bubble("SYSTEM", f"Voice changed to {key}", "system")

    def _toggle_gender(self):
        res = self.voice.toggle_gender()
        idx = self.voice_keys.index(self.voice.current_voice) if self.voice.current_voice in self.voice_keys else 0
        self.voice_combo.current(idx)
        self._add_bubble("SYSTEM", res, "system")

    def _stop_voice(self):
        self.voice.stop_speaking()
        self._pulse_status(GREEN, active=False)
        self._set_status("SYSTEM ONLINE", GREEN)

    # ============================================
    # PANELS & DIALOGS
    # ============================================

    def _open_settings(self):
        """Open settings dialog."""
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.geometry("450x400")
        win.configure(bg=BG_PANEL)
        win.transient(self.root)
        win.grab_set()

        tk.Label(win, text="⚙️ Settings", font=("Segoe UI", 16, "bold"),
                 fg=CYAN, bg=BG_PANEL).pack(pady=15)

        # Voice settings
        frame1 = tk.LabelFrame(win, text=" Voice ", font=("Segoe UI", 10, "bold"),
                                fg=WHITE, bg=BG_CARD, padx=15, pady=10)
        frame1.pack(fill=tk.X, padx=20, pady=5)

        tk.Label(frame1, text=f"Current: {self.voice.current_voice}",
                 font=("Segoe UI", 10), fg=GREEN, bg=BG_CARD).pack(anchor="w")
        tk.Label(frame1, text=f"TTS: {'Active' if self.voice.tts_available else 'Inactive'}",
                 font=("Segoe UI", 10), fg=WHITE, bg=BG_CARD).pack(anchor="w")
        tk.Label(frame1, text=f"STT: {'Active' if self.voice.stt_available else 'Inactive'}",
                 font=("Segoe UI", 10), fg=WHITE, bg=BG_CARD).pack(anchor="w")

        # Model settings
        frame2 = tk.LabelFrame(win, text=" AI Models ", font=("Segoe UI", 10, "bold"),
                                fg=WHITE, bg=BG_CARD, padx=15, pady=10)
        frame2.pack(fill=tk.X, padx=20, pady=5)

        for m in self.registry.models.values():
            status = "🟢" if m.is_available else "🔴"
            tk.Label(frame2, text=f"{status} {m.display_name}",
                     font=("Segoe UI", 10), fg=WHITE, bg=BG_CARD).pack(anchor="w")

        # Security settings
        frame3 = tk.LabelFrame(win, text=" Security ", font=("Segoe UI", 10, "bold"),
                                fg=WHITE, bg=BG_CARD, padx=15, pady=10)
        frame3.pack(fill=tk.X, padx=20, pady=5)

        tk.Label(frame3, text="🛡️ Zero-trust mode: Active",
                 font=("Segoe UI", 10), fg=GREEN, bg=BG_CARD).pack(anchor="w")
        tk.Label(frame3, text="🔒 Secret detection: Active",
                 font=("Segoe UI", 10), fg=GREEN, bg=BG_CARD).pack(anchor="w")
        tk.Label(frame3, text=f"📝 {self.gateway.get_report()}",
                 font=("Segoe UI", 9), fg=GRAY, bg=BG_CARD).pack(anchor="w")

        tk.Button(win, text="Close", font=("Segoe UI", 10),
                  bg=GRAY_DIM, fg=WHITE, relief=tk.FLAT, padx=20,
                  command=win.destroy).pack(pady=15)

    def _show_memory_stats(self):
        """Show memory statistics dialog."""
        stats = self.memory.get_stats()
        messagebox.showinfo(
            "Memory Stats",
            f"🧠 Memory Statistics\n\n"
            f"Messages: {stats['messages']}\n"
            f"Facts: {stats['facts']}\n"
            f"Sessions: {stats['sessions']}\n"
            f"Pending Tasks: {stats.get('pending_tasks', 0)}"
        )

    def _show_model_stats(self):
        """Show model telemetry."""
        report = self.router.get_report()
        messagebox.showinfo("Model Telemetry", report)

    # ============================================
    # SHUTDOWN
    # ============================================

    def _shutdown(self):
        farewell = "Shutting down. Goodbye, Sir."
        self._add_bubble("JARVIS", farewell, "jarvis")
        self.voice.speak(farewell)
        self.bridge.stop()
        self.memory.close()
        self.root.after(2000, lambda: (self.root.destroy(), sys.exit(0)))


def main():
    root = tk.Tk()
    app = JarvisGUI(root)
    root.protocol("WM_DELETE_WINDOW", app._shutdown)
    root.mainloop()


if __name__ == "__main__":
    main()