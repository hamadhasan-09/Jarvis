"""Local Indic Parler-TTS provider for English and Urdu speech."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from voice.config import VoiceConfig, load_config

LOG = logging.getLogger("jarvis.voice.tts")


class IndicParlerTTS:
    def __init__(self, config: VoiceConfig | None = None, *, model=None, tokenizer=None, torch_module=None):
        self.config = config or load_config()
        self.settings = self.config.section("tts")
        self._model = model
        self._tokenizer = tokenizer
        self._description_tokenizer = None
        self._torch = torch_module
        self.device = str(self.settings.get("device", "auto"))
        self.sample_rate = 24000

    def load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from parler_tts import ParlerTTSForConditionalGeneration
            from transformers import AutoTokenizer
        except ImportError as exc:
            raise RuntimeError("Indic Parler-TTS requires torch, transformers, and parler-tts") from exc
        self._torch = torch
        if self.device == "auto":
            enough_memory = False
            if torch.cuda.is_available():
                try:
                    free_bytes, _total_bytes = torch.cuda.mem_get_info()
                    enough_memory = free_bytes >= float(self.settings.get("minimum_gpu_memory_gb", 3.5)) * (1024 ** 3)
                except Exception:
                    enough_memory = False
            self.device = "cuda" if enough_memory else "cpu"
        model_id = str(self.settings.get("model", "ai4bharat/indic-parler-tts"))
        self._model = ParlerTTSForConditionalGeneration.from_pretrained(model_id).to(self.device)
        self._tokenizer = AutoTokenizer.from_pretrained(model_id)
        description_model = str(self._model.config.text_encoder._name_or_path)
        self._description_tokenizer = AutoTokenizer.from_pretrained(description_model)
        self._model.eval()
        self.sample_rate = int(self._model.audio_encoder.config.sampling_rate)

    def synthesize(self, text: str, lang: str) -> np.ndarray:
        self.load()
        language = "ur" if lang == "ur" else "en"
        description = str(self.settings.get("voices", {}).get(language, ""))
        seed = int(self.settings.get("seed", 42))
        torch = self._torch
        torch.manual_seed(seed)
        if self.device.startswith("cuda") and torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
        description_inputs = self._description_tokenizer(description, return_tensors="pt")
        prompt_inputs = self._tokenizer(str(text), return_tensors="pt")
        inputs = {
            "input_ids": description_inputs.input_ids.to(self.device),
            "attention_mask": description_inputs.attention_mask.to(self.device),
            "prompt_input_ids": prompt_inputs.input_ids.to(self.device),
            "prompt_attention_mask": prompt_inputs.attention_mask.to(self.device),
            "do_sample": True,
        }
        try:
            with torch.inference_mode():
                audio = self._model.generate(**inputs)
        except RuntimeError as exc:
            if self.device.startswith("cuda") and "out of memory" in str(exc).lower():
                LOG.warning("Parler GPU memory exhausted; retrying synthesis on CPU")
                torch.cuda.empty_cache()
                self.device = "cpu"
                self._model = self._model.to("cpu")
                for key in ("input_ids", "attention_mask", "prompt_input_ids", "prompt_attention_mask"):
                    inputs[key] = inputs[key].to("cpu")
                with torch.inference_mode():
                    audio = self._model.generate(**inputs)
            else:
                raise
        waveform = audio.detach().float().cpu().numpy().reshape(-1)
        if waveform.size == 0:
            raise RuntimeError("Parler-TTS generated an empty waveform")
        return waveform

    def warmup(self) -> None:
        self.synthesize("Hello.", "en")
        self.synthesize("السلام علیکم۔", "ur")

    def close(self) -> None:
        self._model = None
        self._tokenizer = None
        self._description_tokenizer = None
