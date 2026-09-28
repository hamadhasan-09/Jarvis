# Voice test fixtures

The real-audio integration cases look for `en.wav` and `ur.wav` in this directory. They are intentionally skipped when either recording is absent; no synthetic speech is substituted for a real microphone fixture.

Provide short, clean recordings as mono, 16-bit PCM WAV at 16 kHz:

- `en.wav`: a simple English greeting such as “Hello, Jarvis.”
- `ur.wav`: a short Urdu greeting recorded in natural Urdu.

Do not put personal or sensitive speech in committed fixtures. Until both samples are added, the non-audio routing, validation, queue, and fake-TTS integration tests remain runnable without model files or network access.
