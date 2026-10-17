# Local voice provider selection

The existing VoiceService, VoiceBrainBridge, AudioOutput, turn ownership, and AEC pipeline remain in use. No second capture or playback path is introduced. `barge_in: false` remains the default.

## Offline defaults

- STT: `provider: faster-whisper`, `model: large-v3`, `device: cpu`, `compute_type: int8`, `language: auto`. `model_path` optionally selects a local CTranslate2 directory relative to the configuration file (absolute paths also work). Otherwise Faster-Whisper uses its existing local Hugging Face cache. Cache resolution and model creation both use `local_files_only=True`; model.bin, config.json, and tokenizer.json must exist before construction to prevent the library's tokenizer download fallback; missing models fail explicitly, with no download or smaller-model fallback.
- TTS: `provider: piper`. `tts.local.models.en` and `.ur` select ONNX files; each requires its adjacent `.onnx.json`. Defaults are `models/tts/en_US-amy-medium.onnx` and `models/tts/ur_PK-aegis_female-medium.onnx`. Paths resolve relative to the configuration file. These binaries remain ignored by Git. Provision them manually or explicitly configure existing absolute paths; this change does not copy assets from the original worktree.
- Piper validates both configured voices at startup, loads each lazily on first synthesis, and returns mono float32 at the fixed configured output rate. Each AudioChunk is resampled when needed, preserving playback/reference timing across cached English/Urdu switches. Piper's optional Arabic diacritizer is disabled to prevent auxiliary downloads.
- Both providers have `warmup_on_startup: false`. Explicitly enabling local warmup allocates models and performs STT warmup inference; it can be very slow and memory intensive on this CPU. Ordinary startup does not load Whisper weights. A missing local model is reported on first transcription or explicit warmup. VAD startup behavior is unchanged.

## Optional online providers

Groq requires BOTH `stt.provider: groq` and `stt.online_enabled: true`, plus an externally configured Groq credential. Edge requires BOTH `tts.provider: edge_tts` and `tts.online_enabled: true`; the existing optional ElevenLabs branch additionally requires its own enabled configuration and credential. Online warmup never makes STT requests. Provider failures do not implicitly select another STT provider/model or enable online TTS. The legacy standalone local_listener is not changed by this restoration; use the VoiceService entry point for these provider settings.

## Runtime prerequisites and safety

Install requirements-voice.txt in a suitable voice environment (Python 3.11/3.12 recommended by the existing dependency file), including Piper 1.8's AudioChunk API, ONNX Runtime/espeak support, Faster-Whisper/CTranslate2, and soxr. The reference environment currently contains Piper 1.8.0 and Faster-Whisper 1.2.1; installed metadata/API inspection does not establish actual inference compatibility. Reconciled worktree has no dedicated venv or provisioned Piper assets. The original worktree contains both voice assets and the user cache contains a large-v3 snapshot (~3.09 GB model.bin); these were not loaded, altered, or integrity-validated.

Before real testing, provision paths/environment, confirm configured-token local voice authentication, choose input/output devices, and authorize a suitable LLM provider separately. Python 3.14/native dependency compatibility and CPU/RAM latency remain unverified. Large-v3 and the separately configured Qalb model can compete for limited RAM; no smaller model is silently substituted. No microphone, real provider, model, production backend, or paid API was exercised during synthetic verification.

`voice.selftest` now provides independent offline file diagnostics using the current adapters. It never starts Nexus, an LLM, a microphone, or playback. Running either complete command below WILL load the chosen real local model; neither was executed during synthetic verification.

```powershell
python -m voice.selftest --provider piper --config C:\Users\Hammad\Desktop\Nexus_reconcile_20261017\voice_config.yaml --language ur --text "سلام۔" --output C:\Temp\nexus-piper.wav
python -m voice.selftest --provider whisper --config C:\Users\Hammad\Desktop\Nexus_reconcile_20261017\voice_config.yaml --input C:\Temp\speech.wav
```

Piper writes a WAV file only. Whisper reads mono uncompressed 16-bit PCM WAV, honors the configured language/model/device/compute type, and prints the transcript and timing. Both commands explicitly select their local adapter and disable online mode in an in-memory configuration copy, even if the application YAML selects a cloud provider. Invalid or missing arguments stop before loading configuration or models. Adapter failures return exit status 1; invalid arguments return 2.

For existing Piper assets, set `tts.local.models.en` and `.ur` in the selected configuration to the absolute paths `C:\Users\Hammad\Desktop\Nexus\models\tts\en_US-amy-medium.onnx` and `C:\Users\Hammad\Desktop\Nexus\models\tts\ur_PK-aegis_female-medium.onnx`. Adjacent JSON files are required. No copying or committing binaries is necessary; configuration paths were left unchanged by the audit correction.

## Synthetic verification (2026-10-17)

Final focused run: **237 passed, 2 skipped, 0 failed** (239 collected; 22.23 seconds). The two existing real-Whisper WAV cases skip because en.wav/ur.wav fixtures were not supplied. Coverage includes fake local/cloud selection, both Piper languages, cached sample-rate switching, missing/corrupt/empty synthesis, explicit warmup, language detection, missing/incomplete Whisper caches, browser pairing, token authentication, revocation, private routing, turn/cancellation ownership, AEC, and desktop-safe startup/HTTP shutdown. Real speech provider imports and external network connections were blocked by the isolated harness. Seven changed Python files passed syntax compilation; git diff --check passed. Original reference files and protected routing/bridge/audio/security files remained unchanged. Evidence is retained locally under `.cache/local-voice-restore/` and is not source or a model asset. No commit or push was made.

## Audit corrections verification (2026-10-17)

ElevenLabs now checks BOTH `tts.online_enabled is True` and `tts.elevenlabs.enabled is True` at every request, before credential access. Strings, integers, nulls, and false values do not authorize requests. This covers direct synthesis and MP3 saving as well as the Edge adapter's optional branch.

Focused synthetic run: **80 passed, 0 skipped, 0 failed** in 2.39 seconds, covering both opt-ins, request-time opt-out, independent offline diagnostics, failure cleanup, input validation, configurable external model paths, and the existing fake-provider suite. Three changed Python files passed syntax checks; whitespace checks passed. The harness blocked real speech-provider imports and external network requests. Original reference and protected authentication/cancellation/audio/configuration files matched their pre-change hashes. No real models or devices were started; no commit or push was made. Evidence: `.cache/voice-audit-fixes/synthetic.log` and `synthetic.xml`.
