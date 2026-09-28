# JARVIS voice service

The active local voice implementation is `voice/` and its single runtime file is `voice_config.yaml`. It supports English and Urdu, keeps audio processing out of the brain/tool modules, and sends recognized user requests through the existing Socket.IO brain contract. During the migration, the previous listener remains in the repository for rollback; it is no longer selected by the normal local voice startup path.

## Machine fit and model constraints

This PC reports a Quadro K620 with 2 GB VRAM, with about 1.45 GB free during inspection. That is not enough for Whisper large-v3 (about 4–5 GB), Indic Parler (about 2–4 GB), and Qalb Q4 (about 5–6 GB) together. The configured STT primary remains `large-v3`; `fallback_model: large-v3-turbo` is selected on CPU when automatic device selection finds insufficient GPU memory. Set `stt.force_primary: true` to force the primary model for testing on capable hardware (or CPU, if explicitly intended). Startup logs report the primary, selected runtime model, and reason. The current defaults also use CPU for models below their configured VRAM thresholds and set Qalb llama.cpp GPU layers to zero. You can adjust `stt.minimum_gpu_memory_gb`, `tts.minimum_gpu_memory_gb`, and `brain.qalb.gpu_layers` in `voice_config.yaml`; reducing Qalb layers will not make an 8B Q4 model fit entirely in 2 GB, so some or all layers must stay in system RAM.

The active project venv is Python 3.14.7 and currently lacks PyTorch, Faster-Whisper, soxr, Silero VAD, Transformers, and Parler-TTS. Use a dedicated Python 3.11 or 3.12 environment for the voice dependencies. Install PyTorch and torchaudio first using the official command that matches your OS and CUDA/CPU target, then install this file. The optional openWakeWord package is not installed by default; install it separately only if you enable wake-word mode:

```cmd
python -m pip install -r requirements-voice.txt
```

```cmd
python -m pip install openwakeword
```

The model is gated by Hugging Face access conditions. Open [the Indic Parler model card](https://huggingface.co/ai4bharat/indic-parler-tts), sign in, accept its access conditions, then run `hf auth login` in the same environment before first startup. The selected checkpoint supports English and Urdu. Its model card lists Mary and Thoma as recommended English voices; the configured English description uses Mary. The Urdu prompt uses a fixed male voice description because the current speaker table does not name a recommended Urdu speaker.

The Parler model is loaded from its configured official model id on first service startup. Qalb uses only `C:\Users\Hammad\Desktop\Nexus\models\qalb\Qalb-1.0-8B-Instruct.Q4_K_M.gguf`; this exact path is configured in `voice_config.yaml`, and JARVIS never downloads or substitutes a Qalb GGUF. Qalb serving uses llama.cpp at port 8081; Ollama is selectable in config. `auto_start` is enabled and uses the configured llama-server executable. Its `-m` argument is exactly the configured model path, `--chat-template llama3` overrides the absent template metadata, and `gpu_layers` is passed as `-ngl`; this PC is configured at 0 GPU layers.

The configured GGUF metadata was inspected directly: architecture `llama`, tokenizer model `gpt2` with `llama-bpe` pre-tokenization, BOS id 128000, EOS id 128001, and Llama 3 `<|eot_id|>` token id 128009. The file has no `tokenizer.chat_template` entry. JARVIS therefore explicitly passes llama.cpp's built-in `llama3` template and sends `<|eot_id|>` / `<|end_of_text|>` as completion stops. The Urdu system prompt is unchanged. Returned text containing any chat-template control token is rejected and regenerated once; it is not silently cleaned into a potentially fabricated multi-turn reply.

## Modes and behavior

- `input.mode: always_on` is the default. `wake_word` requires `wakeword.enabled: true` and the optional openWakeWord package. `push_to_talk` is exposed through `VoiceService.set_push_to_talk()` for a host UI to control.
- `wakeword.enabled` defaults to false.
- `barge_in` defaults to false. Enable it only with a headset or echo cancellation; speakers can make JARVIS interrupt itself.
- Whisper tries the configured large-v3 model, detects English versus Urdu (treating Hindi language detection probability as Urdu), then forces the selected language if needed. Hallucinations, low-confidence segments, silence, and remaining Devanagari are discarded before the brain callback.
- `brain.qalb.mode: hybrid` is the default. The existing brain handles tools/actions, then Qalb writes the spoken Urdu response. `direct` sends Urdu conversation directly to Qalb with no tool interface. Qalb output is retried once if it is empty, repetitive, mostly Latin, contains Devanagari, or leaks chat-template tokens. After Qalb fails, hybrid mode asks the existing brain to rephrase its completed result in Urdu; English uses the existing brain unchanged.
- Rumik is not part of this replacement path.

## Run and diagnose

Start JARVIS with `start.bat`. If the new Python environment is separate, activate it before launching the server so the server and voice process share its packages. The service validates `voice_config.yaml`, loads models once, warms up STT/TTS, and logs latency and detected language. Qalb's model path is still pending until you set it.

Run the hardware/model self-test from the project root:

```cmd
python -m voice.selftest
```

It lists devices, records and resamples three seconds of microphone audio, plays that audio back, transcribes it, and synthesizes one English and one Urdu utterance. It needs a working mic/speaker, model dependencies, model access, and enough system RAM. It does not test a real Qalb GGUF while its path/server is unconfigured.

If startup reports a missing dependency, use the dedicated environment rather than installing packages into Python 3.14. If CUDA memory errors occur, lower `brain.qalb.gpu_layers`, increase `stt.minimum_gpu_memory_gb` or `tts.minimum_gpu_memory_gb` to force CPU, and keep at least one model off GPU. When Qalb is offline or unset, English remains available and Urdu hybrid/direct requests fall back to the main brain with an Urdu-script instruction.
