"""
JARVIS v5.0 - Autonomous Voice & AI Employee (Debug & UTF-8 Hardened)
"""

import sys
import os

# Force Python into UTF-8 Mode globally
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

if sys.platform == "win32":
    try:
        sys.stdin.reconfigure(encoding='utf-8', errors='replace')
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import signal
import traceback
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from colorama import init, Fore, Style

init(autoreset=True)
load_dotenv()

# Core reliability
from core.error_handler import ErrorHandler
from core.memory_manager import PersistentMemory
from core.rate_limiter import RateLimiter

# Security & Keys
from security_v2.key_manager import SecureKeyManager
from security_v2.prompt_defense import PromptDefense
from security_v2.sandbox import CommandSandbox
from modules.human_approval import ApprovalGateway

# Multi-Model Layer
from models.model_registry import ModelRegistry
from models.model_router import ModelRouter

# Autonomous Engine
from engine.planner import Planner
from engine.executor import Executor
from engine.verifier import Verifier
from engine.learner import Learner
from engine.orchestrator import Orchestrator

# Voice Engine
from voice_engine import VoiceEngine


class JARVIS:
    """Autonomous AI Employee with Voice Interaction."""

    def __init__(self):
        print(Fore.CYAN + "=" * 60)
        print("  J.A.R.V.I.S. v5.0 (Nexus) - Voice & Autonomous AI")
        print("=" * 60 + Style.RESET_ALL)

        # 1. Voice Engine
        print(Fore.YELLOW + "\n[1/6] Initializing Voice Engine..." + Style.RESET_ALL)
        self.voice = VoiceEngine()

        # 2. Core Systems
        print(Fore.YELLOW + "[2/6] Initializing Memory & Reliability..." + Style.RESET_ALL)
        self.errors = ErrorHandler()
        self.memory = PersistentMemory()
        self.rate_limiter = RateLimiter()

        # 3. Security
        print(Fore.YELLOW + "[3/6] Initializing Security Layer..." + Style.RESET_ALL)
        self.keys = SecureKeyManager()
        self.prompt_defense = PromptDefense()
        self.sandbox = CommandSandbox()
        self.gateway = ApprovalGateway()

        # Check API keys
        available_keys = self.keys.list_available()
        if not available_keys:
            print(Fore.RED + "\n❌ No API keys found in .env!" + Style.RESET_ALL)
            self.keys.setup_wizard()
            available_keys = self.keys.list_available()
            if not available_keys:
                print(Fore.RED + "Cannot start without at least one API key." + Style.RESET_ALL)
                sys.exit(1)

        # 4. Models
        print(Fore.YELLOW + "[4/6] Loading AI Models..." + Style.RESET_ALL)
        self.registry = ModelRegistry(self.keys)
        self.router = ModelRouter(self.registry)

        # 5. Engine
        print(Fore.YELLOW + "[5/6] Starting Autonomous Subsystems..." + Style.RESET_ALL)
        self.planner = Planner(self.router)
        self.executor = Executor(self.registry, self.gateway)
        self.verifier = Verifier(self.router)
        self.learner = Learner()

        self.orchestrator = Orchestrator(
            planner=self.planner,
            router=self.router,
            executor=self.executor,
            verifier=self.verifier,
            learner=self.learner,
            memory=self.memory,
            gateway=self.gateway,
            errors=self.errors,
        )

        # 6. Online greeting
        print(Fore.YELLOW + "[6/6] Finalizing Startup..." + Style.RESET_ALL)
        stats = self.memory.get_stats()
        print(Fore.GREEN + f"\n✅ JARVIS Online!" + Style.RESET_ALL)
        print(f"  🧠 Memory: {stats['messages']} messages across {stats['sessions']} sessions")

        greeting = "Systems online. Good day, Sir. How may I assist you?"
        self.voice.speak(greeting)

    def _signal_handler(self, signum, frame):
        self._shutdown()

    def _shutdown(self):
        farewell = "Shutting down systems. Goodbye, Sir."
        print(Fore.CYAN + f"\n🔊 JARVIS: {farewell}" + Style.RESET_ALL)
        self.voice.speak(farewell)
        self.memory.close()
        sys.exit(0)

    def _is_objective(self, text):
        text_lower = text.lower().strip()
        conversational_starts = (
            "hello", "hi", "hey", "remember", "what is", "who is", "where is",
            "why is", "how are", "can you tell", "tell me", "thank", "good morning",
            "do you remember", "what do you know", "how do you"
        )
        if any(text_lower.startswith(prefix) for prefix in conversational_starts):
            return False

        action_verbs = [
            "research and", "build a", "create a full", "develop a", "deploy",
            "analyze competitors", "write a comprehensive report", "scrape",
            "generate website", "design architecture"
        ]
        return any(v in text_lower for v in action_verbs)

    def _print_help(self):
        print(Fore.CYAN + """
╔════════════════════════════════════════════════════════════╗
║              JARVIS v5.0 - Voice & Command Menu            ║
╠════════════════════════════════════════════════════════════╣
║  • Press ENTER on empty prompt to activate MICROPHONE 🎤   ║
║  • Type any message to chat by TEXT ⌨️                      ║
║  • JARVIS will speak all answers out loud 🔊               ║
║                                                            ║
║  COMMANDS:                                                 ║
║    status    - Active/completed objectives                 ║
║    models    - View connected AI models                    ║
║    memory    - View memory stats                           ║
║    security  - View security approval logs                 ║
║    keys      - Manage API keys                             ║
║    exit      - Shutdown                                    ║
╚════════════════════════════════════════════════════════════╝
""" + Style.RESET_ALL)

    def start(self):
        try:
            signal.signal(signal.SIGINT, self._signal_handler)
        except Exception:
            pass

        self._print_help()

        while True:
            try:
                user_input = input(Fore.GREEN + "\n🎯 You (Press Enter for 🎤 Voice, or Type): " + Style.RESET_ALL).strip()

                if not user_input:
                    if self.voice.stt_available:
                        user_input = self.voice.listen()
                    if not user_input:
                        continue

                cmd = user_input.lower()

                if cmd in ('exit', 'quit', 'bye'):
                    self._shutdown()
                    break
                elif cmd == 'help':
                    self._print_help()
                    continue
                elif cmd == 'status':
                    print(self.orchestrator.get_status())
                    continue
                elif cmd == 'models':
                    print(self.router.get_report())
                    continue
                elif cmd == 'learning':
                    print(self.learner.get_report())
                    continue
                elif cmd == 'memory':
                    stats = self.memory.get_stats()
                    print(f"\n🧠 Messages: {stats['messages']} | Facts: {stats['facts']} | Sessions: {stats['sessions']}")
                    continue
                elif cmd == 'usage':
                    print(self.rate_limiter.get_report())
                    continue
                elif cmd == 'security':
                    print(self.gateway.get_report())
                    continue
                elif cmd == 'errors':
                    print(self.errors.get_report())
                    continue
                elif cmd == 'keys':
                    self.keys.setup_wizard()
                    continue

                defense = self.prompt_defense.analyze(user_input)
                if not defense["is_safe"]:
                    print(Fore.RED + "🛡️ Potential prompt injection detected. Sanitizing..." + Style.RESET_ALL)
                    user_input = self.prompt_defense.sanitize(user_input)

                allowed, reason = self.rate_limiter.check()
                if not allowed:
                    print(Fore.YELLOW + f"⚠️ {reason}" + Style.RESET_ALL)
                    continue

                if self._is_objective(user_input):
                    print(Fore.CYAN + "\n🤖 Objective detected. Engaging Autonomous Engine..." + Style.RESET_ALL)
                    self.voice.speak("Objective received. Initializing autonomous execution plan.")

                    obj = self.orchestrator.receive(user_input)
                    result = self.orchestrator.execute(obj)

                    if result["success"]:
                        msg = f"Objective completed successfully in {result['duration']}."
                        print(Fore.GREEN + f"\n✅ {msg}" + Style.RESET_ALL)
                        self.voice.speak(msg)
                    else:
                        msg = f"Objective failed: {result.get('error', 'Unknown Error')}"
                        print(Fore.RED + f"\n❌ {msg}" + Style.RESET_ALL)
                        self.voice.speak("I encountered an issue executing the objective.")
                else:
                    choice = self.router.select("fast_query")
                    print(Fore.YELLOW + f"  (Routing to: {choice['reason']})" + Style.RESET_ALL)

                    system = "You are JARVIS, Tony Stark's witty, highly intelligent AI personal assistant. Be concise, brilliant, and speak directly to the user."

                    context = ""
                    try:
                        context = self.memory.get_context(user_input)
                    except Exception as mem_err:
                        print(Fore.RED + f"Memory search error (ignored): {mem_err}" + Style.RESET_ALL)

                    if context:
                        system += f"\n\nContext from memory:\n{context}"

                    response = self.registry.generate(
                        choice["model"], user_input, system,
                        max_tokens=2000, temperature=0.7
                    )

                    print(Fore.CYAN + f"\n🔊 JARVIS: {response}" + Style.RESET_ALL)

                    # Speak response out loud
                    self.voice.speak(response)

                    # Save to memory
                    try:
                        self.memory.save_message("chat", "user", user_input)
                        self.memory.save_message("chat", "assistant", response)
                        if "remember" in user_input.lower():
                            self.memory.save_fact("user_pref", user_input)
                    except Exception as save_err:
                        print(Fore.RED + f"Memory save error (ignored): {save_err}" + Style.RESET_ALL)

                    self.rate_limiter.record()

            except KeyboardInterrupt:
                self._shutdown()
                break
            except Exception as e:
                print(Fore.RED + "\n❌ Full Error Traceback:" + Style.RESET_ALL)
                traceback.print_exc()


if __name__ == "__main__":
    try:
        jarvis = JARVIS()
        jarvis.start()
    except Exception as e:
        print(f"\n❌ Fatal Error: {e}")
        import traceback
        traceback.print_exc()