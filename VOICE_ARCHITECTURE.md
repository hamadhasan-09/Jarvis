# Voice architecture

- **Live/primary:** voice/, started when JARVIS_VOICE_ENGINE=true or JARVIS_LOCAL_VOICE=true.
- **Fallback/manual:** voice/local_listener.py, run standalone.
- **Archived:** archive/voice_engine_unused/, kept for reference and not wired into the app.