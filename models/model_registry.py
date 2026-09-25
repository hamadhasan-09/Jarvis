"""
Multi-Model Registry - 100% Pure Groq Edition
"""

import time
import os
from groq import Groq


class ModelConfig:
    def __init__(self, id, model_name, display_name, capabilities=None):
        self.id = id
        self.provider = "groq"
        self.model_name = model_name
        self.display_name = display_name
        self.capabilities = capabilities or []
        self.is_available = True
        self.total_calls = 0
        self.total_successes = 0
        self.total_failures = 0
        self.avg_latency_ms = 250


class ModelRegistry:
    """Registry powered exclusively by Groq."""

    def __init__(self, key_manager):
        self.keys = key_manager
        self.models = {}
        self.client = None
        self._register_models()
        self._init_groq()

    def _register_models(self):
        """Register your account's exact models."""
        models = [
            ModelConfig(
                "groq_fast",
                "openai/gpt-oss-20b",
                "GPT-OSS 20B (Groq Fast)",
                ["fast", "chat", "cheap"]
            ),
            ModelConfig(
                "groq_heavy",
                "openai/gpt-oss-120b",
                "GPT-OSS 120B (Groq Heavy Reasoning)",
                ["reasoning", "planning", "code", "creative"]
            ),
            ModelConfig(
                "groq_qwen",
                "qwen/qwen3.8-27b",
                "Qwen 27B (Groq Logic & Code)",
                ["code", "logic", "reasoning"]
            ),
        ]
        for m in models:
            self.models[m.id] = m

    def _init_groq(self):
        """Initialize the Groq client."""
        key = self.keys.get_key("groq")
        if key:
            try:
                self.client = Groq(api_key=key)
                print("  ✅ Groq Connected (100% Groq Mode Active)")
            except Exception as e:
                print(f"  ⚠️ Groq init error: {e}")

    def generate(self, model_id, prompt, system="", max_tokens=2000, temperature=0.7):
        """Generate a response using Groq."""
        if not self.client:
            raise ValueError("Groq client is not initialized. Check your GROQ_API_KEY.")

        model = self.models.get(model_id, self.models.get("groq_fast"))

        start = time.time()
        model.total_calls += 1

        try:
            resp = self.client.chat.completions.create(
                model=model.model_name,
                messages=[
                    {"role": "system", "content": system or "You are JARVIS, an autonomous AI employee."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=max_tokens,
                temperature=temperature
            )
            result = resp.choices[0].message.content

            model.total_successes += 1
            latency = int((time.time() - start) * 1000)
            model.avg_latency_ms = int(model.avg_latency_ms * 0.9 + latency * 0.1)
            return result

        except Exception as e:
            model.total_failures += 1
            raise

    def get_available(self, capability=None):
        return list(self.models.values())