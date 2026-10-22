"""Langkah 1 — Inisialisasi: manajemen multi-API key & pengaturan sistem.

API key disimpan di ~/.ai_director/config.json (di luar repo, tidak ikut ter-bundle
ke dalam .exe) dan bisa juga disuplai lewat variabel lingkungan:
  OPENAI_API_KEY / GEMINI_API_KEY / GROQ_API_KEY / DEEPSEEK_API_KEY
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

APP_NAME = "AI Auto-Director Engine"
CONFIG_DIR = Path(os.environ.get("AI_DIRECTOR_HOME", str(Path.home() / ".ai_director")))
CONFIG_FILE = CONFIG_DIR / "config.json"
WORKSPACE = Path(os.environ.get("AI_DIRECTOR_WORKSPACE", str(Path.cwd() / "projects")))

PROVIDERS = ["openai", "gemini", "groq", "deepseek", "ollama"]
ENV_KEYS = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}

DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "gemini": "gemini-1.5-flash",
    "groq": "llama-3.3-70b-versatile",
    "deepseek": "deepseek-chat",
    "ollama": "llama3.1",
}


@dataclass
class Settings:
    api_keys: dict = field(default_factory=dict)          # provider -> key
    provider_order: list = field(default_factory=lambda: ["openai", "gemini", "groq", "deepseek", "ollama"])
    whisper_model: str = "base"                            # tiny|base|small|medium|large-v3
    hw_encoder: str = "auto"                               # auto|nvenc|qsv|amf|libx264
    target_format: str = "shorts"                          # shorts|long
    preset: str = "split_v"                                # split_v | pip | fullscreen_target
    max_shots: int = 8                                     # jumlah segmen reaksi untuk Shorts
    long_duration_s: int = 480                             # durasi target video panjang (8 menit)

    # ------------------------------------------------------------------ load/save
    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if hasattr(s, k):
                        setattr(s, k, v)
            except Exception:
                pass
        # environment overrides
        for prov, env in ENV_KEYS.items():
            val = os.environ.get(env)
            if val:
                s.api_keys[prov] = val
        return s

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    # ------------------------------------------------------------------ helpers
    def active_provider(self) -> str | None:
        for p in self.provider_order:
            if self.api_keys.get(p):
                return p
        if os.environ.get("OLLAMA_HOST") or self.api_keys.get("ollama"):
            return "ollama"
        return None

    def set_key(self, provider: str, key: str) -> None:
        if provider == "ollama":
            os.environ.setdefault("OLLAMA_HOST", "http://localhost:11434")
            self.api_keys["ollama"] = "local"
        else:
            self.api_keys[provider] = key.strip()
        self.save()

    def project_dir(self, slug: str) -> Path:
        d = WORKSPACE / slug
        d.mkdir(parents=True, exist_ok=True)
        return d
