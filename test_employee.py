import sys
import os
from pathlib import Path
from dotenv import load_dotenv

# Fix windows console emoji printing
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Setup paths
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))
load_dotenv(ROOT_DIR / ".env")

from core.memory_manager import PersistentMemory
from llm import ProviderManager, TaskRouter
from engine.orchestrator import Orchestrator
from engine.planner import Planner
from engine.executor import Executor
from engine.verifier import Verifier
from engine.learner import Learner

class ConsoleGateway:
    """A simple gateway for testing the engine in the console."""
    def request_approval(self, action_type, context, prompt_text):
        print(f"\n[APPROVAL NEEDED - {action_type.upper()}]")
        print(prompt_text)
        return True # Auto-approve for test
        
    def log(self, message):
        print(message)

def run_test():
    print("Initializing Advanced Employee Engine...")
    memory = PersistentMemory(project_root=ROOT_DIR)
    provider_manager = ProviderManager(project_root=ROOT_DIR)
    task_router = TaskRouter(provider_manager=provider_manager)
    gateway = ConsoleGateway()
    
    orchestrator = Orchestrator(
        planner=Planner(provider_manager, task_router),
        router=task_router,
        executor=Executor(provider_manager, gateway, workspace_dir=str(ROOT_DIR)),
        verifier=Verifier(provider_manager, task_router),
        learner=Learner(),
        memory=memory,
        gateway=gateway,
        errors=None
    )
    
    objective = "Create a simple web app that displays a digital clock. Put the files in a folder called 'clock_app'."
    
    print(f"\nTesting Objective: '{objective}'\n")
    print("Sending to Orchestrator...\n" + "-"*50)
    
    obj = orchestrator.receive(objective)
    result = orchestrator.execute(obj)
    
    print("\n" + "-"*50)
    print("Result:", result)

if __name__ == "__main__":
    run_test()
