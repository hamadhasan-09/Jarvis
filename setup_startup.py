"""
JARVIS Windows Startup Configurator
Enables / Disables JARVIS starting automatically when Windows boots.
"""

import os
import sys
from pathlib import Path


def enable_startup():
    startup_folder = Path(os.getenv("APPDATA")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    shortcut_vbs = startup_folder / "Nexus_JARVIS.vbs"
    
    current_dir = Path(__file__).parent.resolve()
    python_exe = current_dir / "venv" / "Scripts" / "pythonw.exe"  # pythonw runs silently without black CMD window
    
    if not python_exe.exists():
        python_exe = Path(sys.executable).parent / "pythonw.exe"
        
    script_path = current_dir / "app_gui.py"

    # VBScript to launch JARVIS in the background seamlessly on Windows login
    vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "{current_dir}"
WshShell.Run """{python_exe}"" ""{script_path}""", 1, False
'''

    shortcut_vbs.write_text(vbs_content)
    print("=" * 55)
    print("  ✅ JARVIS AUTO-START ENABLED")
    print("=" * 55)
    print(f"Created startup hook at:\n{shortcut_vbs}")
    print("\nJARVIS will now automatically launch and greet you whenever you start your computer!")


def disable_startup():
    startup_folder = Path(os.getenv("APPDATA")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    shortcut_vbs = startup_folder / "Nexus_JARVIS.vbs"
    
    if shortcut_vbs.exists():
        shortcut_vbs.unlink()
        print("✅ JARVIS auto-start has been disabled.")
    else:
        print("ℹ️ Auto-start was not enabled.")


if __name__ == "__main__":
    print("1. Enable Auto-Start on Windows Boot")
    print("2. Disable Auto-Start")
    choice = input("Enter 1 or 2: ").strip()
    
    if choice == "1":
        enable_startup()
    elif choice == "2":
        disable_startup()