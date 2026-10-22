"""Pemrosesan audio: Whisper lokal (faster-whisper) + parser subtitle VTT/SRT.

Semua fungsi mengembalikan bentuk seragam:
  segments = [{"start": float, "end": float, "text": str}]
  words    = [{"start": float, "end": float, "word": str}]
"""
from __future__ import annotations

import re
from pathlib import Path

_MODEL_CACHE = {}


def _get_model(name: str):
    from faster_whisper import WhisperModel
    if name not in _MODEL_CACHE:
        try:
            _MODEL_CACHE[name] = WhisperModel(name, device="cuda", compute_type="float16")
        except Exception:
            _MODEL_CACHE[name] = WhisperModel(name, device="cpu", compute_type="int8")
    return _MODEL_CACHE[name]


def transcribe(audio_path: str | Path, model_name: str = "base",
               language: str | None = None) -> tuple[list[dict], list[dict]]:
    """Transkripsi Whisper lokal dengan word-level timestamps."""
    model = _get_model(model_name)
    segs_out, words_out = [], []
    segments, _info = model.transcribe(str(audio_path), word_timestamps=True,
                                       language=language, vad_filter=True)
    for s in segments:
        segs_out.append({"start": round(s.start, 3), "end": round(s.end, 3),
                         "text": s.text.strip()})
        for w in (s.words or []):
            words_out.append({"start": round(w.start, 3), "end": round(w.end, 3),
                              "word": w.word.strip()})
    return segs_out, words_out


# ----------------------------------------------------------------- subtitle parsing
_TS_RE = r"(\d+):(\d+):(\d+)[.,](\d+)"


def _ts_to_s(h: str, m: str, sec: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000.0


def parse_vtt(text: str) -> list[dict]:
    out = []
    block_re = re.compile(
        rf"({_TS_RE}\s*-->\s*{_TS_RE})[^\n]*\n(.*?)(?=\n\n|\Z)", re.S)
    for _, h1, m1, s1, ms1, h2, m2, s2, ms2, body in block_re.findall(text):
        body = re.sub(r"<[^>]+>", "", body).strip().replace("&nbsp;", " ")
        if body:
            out.append({"start": _ts_to_s(h1, m1, s1, ms1),
                        "end": _ts_to_s(h2, m2, s2, ms2), "text": body})
    return out


def parse_srt(text: str) -> list[dict]:
    return parse_vtt(text)  # format timestamp identik untuk regex kita


def load_captions(path: str | Path) -> list[dict]:
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="ignore")
    return parse_vtt(text) if text.lstrip().startswith(("WEBVTT", "00:", "0")) else parse_srt(text)


def segments_from_source(video_path: Path, settings, log=print) -> tuple[list[dict], list[dict]]:
    """Ambil segmen+words dari subtitle bila ada; kalau tidak -> Transkripsi Darurat Whisper."""
    base = Path(video_path)
    for ext in (".vtt", ".srt"):
        cap = base.with_suffix(ext)
        if cap.exists():
            log(f"   📝 Subtitle ditemukan: {cap.name}")
            segs = load_captions(cap)
            words = segs_to_words(segs)
            return segs, words
    log("   🎧 Tidak ada subtitle → Transkripsi Darurat Whisper lokal…")
    return transcribe(base, settings.whisper_model)


def segs_to_words(segs: list[dict]) -> list[dict]:
    """Distribusi kasar kata per segmen (untuk mode subtitle)."""
    words = []
    for s in segs:
        toks = s["text"].split()
        if not toks:
            continue
        dur = max(s["end"] - s["start"], 0.1) / len(toks)
        t = s["start"]
        for w in toks:
            words.append({"start": round(t, 3), "end": round(t + dur, 3), "word": w})
            t += dur
    return words


def text_at(words: list[dict], t0: float, t1: float) -> str:
    return " ".join(w["word"] for w in words if w["start"] >= t0 - 0.05 and w["end"] <= t1 + 0.05)
