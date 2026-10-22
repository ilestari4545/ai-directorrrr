"""Jembatan LLM multi-provider (OpenAI / Gemini / Groq / DeepSeek / Ollama).

Semua fungsi memakai pola: coba provider aktif sesuai urutan, kalau tidak ada
API key sama sekali -> fallback heuristik lokal agar pipeline tetap jalan.
Output dijamin berupa JSON murni (parse_json_block).
"""
from __future__ import annotations

import json
import os
import re
import urllib.request

from .config import Settings, DEFAULT_MODELS


class LLMError(RuntimeError):
    pass


def _post(url: str, payload: dict, headers: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# --------------------------------------------------------------------- providers
def _chat_openai(base: str, key: str, model: str, system: str, user: str) -> str:
    data = _post(f"{base}/chat/completions", {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.7,
    }, {"Authorization": f"Bearer {key}"})
    return data["choices"][0]["message"]["content"]


def _chat_gemini(key: str, model: str, system: str, user: str) -> str:
    data = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
        {"systemInstruction": {"parts": [{"text": system}]},
         "contents": [{"parts": [{"text": user}]}],
         "generationConfig": {"temperature": 0.7}}, {}, timeout=120)
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _chat_ollama(model: str, system: str, user: str) -> str:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    data = _post(f"{host}/api/chat", {
        "model": model, "stream": False,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}]})
    return data["message"]["content"]


def chat(settings: Settings, system: str, user: str, expect_json: bool = True) -> str:
    """Kirim prompt ke provider aktif; lempar LLMError jika tak ada."""
    prov = settings.active_provider()
    if not prov:
        raise LLMError("Tidak ada API key terpasang (Settings → API Keys).")
    model = DEFAULT_MODELS.get(prov, "")
    key = settings.api_keys.get(prov, "")
    try:
        if prov == "openai":
            return _chat_openai("https://api.openai.com/v1", key, model, system, user)
        if prov == "groq":
            return _chat_openai("https://api.groq.com/openai/v1", key, model, system, user)
        if prov == "deepseek":
            return _chat_openai("https://api.deepseek.com/v1", key, model, system, user)
        if prov == "gemini":
            return _chat_gemini(key, model, system, user)
        if prov == "ollama":
            return _chat_ollama(model, system, user)
    except Exception as e:
        raise LLMError(f"Provider '{prov}' gagal: {e}")
    raise LLMError(f"Provider tidak dikenal: {prov}")


def ask_json(settings: Settings, system: str, user: str) -> dict | list:
    """Wajib mengembalikan objek JSON. Jika LLM unavailable -> LLMError."""
    raw = chat(settings, system + "\nBalas HANYA dengan JSON valid, tanpa markdown.", user)
    return parse_json_block(raw)


def parse_json_block(text: str):
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    # ambil blok {...} atau [...] terluar
    for opener, closer in (("{", "}"), ("[", "]")):
        s = t.find(opener)
        e = t.rfind(closer)
        if s != -1 and e > s:
            try:
                return json.loads(t[s:e + 1])
            except Exception:
                continue
    raise LLMError("Respons LLM bukan JSON valid:\n" + text[:400])
