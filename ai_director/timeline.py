"""Langkah 2 — Otak Penyutradaraan AI: menyusun JSON Timeline Engine.

Output: kerangka instruksi mesin yang mengatur tiga hal mutlak:
  1. Kapan video target diputar            (play_start / play_end)
  2. Kapan video target dijeda otomatis    (pause_at)
  3. Teks narasi yang muncul saat dijeda   (narration) → dibaca di teleprompter
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import Settings
from . import llm

SYSTEM = (
    "Kamu adalah sutradara konten reaksi profesional untuk YouTube Shorts/TikTok. "
    "Diberikan transkrip video target ber-stempel waktu, pilih momen paling memicu "
    "reaksi (lucu, kontroversial, edukatif, plot-twist). Untuk tiap segmen hasilkan: "
    "play window (detik), titik auto-pause di dalam window, dan naskah reaksi singkat "
    "berbahasa Indonesia yang akan dibacakan kreator saat video dijeda. "
    "Narasi harus punchy, 1-3 kalimat, gaya bicara natural."
)


def build_timeline(settings: Settings, project: dict, segments: list[dict],
                   log=print) -> dict:
    """project = {"video": Path, "duration": float, ...} dari langkah ingesti."""
    fmt = settings.target_format
    budget = 60.0 if fmt == "shorts" else float(settings.long_duration_s)
    max_shots = settings.max_shots if fmt == "shorts" else 24

    transcript = _fmt_transcript(segments)
    timeline = None
    try:
        data = llm.ask_json(settings, SYSTEM, _user_prompt(transcript, budget, max_shots))
        timeline = _validate(data, segments, float(project.get("duration") or 0), budget)
        log(f"   🧠 Timeline disusun oleh LLM ({settings.active_provider()}): "
            f"{len(timeline['shots'])} segmen")
    except llm.LLMError as e:
        log(f"   ⚠️  {e}\n   ↪️  Menggunakan sutradara heuristik lokal…")
        timeline = heuristic_direct(segments, budget, max_shots,
                                    float(project.get("duration") or 0))

    timeline.update({
        "format": fmt,
        "budget_seconds": budget,
        "source_video": str(project["video"]),
        "source_duration": float(project.get("duration") or 0),
        "title": project.get("title", ""),
    })
    return timeline


# ---------------------------------------------------------------------- LLM path
def _fmt_transcript(segments: list[dict]) -> str:
    lines = []
    for s in segments:
        lines.append(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}")
    txt = "\n".join(lines)
    return txt[:14000]  # jaga ukuran prompt


def _user_prompt(transcript: str, budget: float, max_shots: int) -> str:
    return (
        f"TOTAL DURASI REAKSI: {budget:.0f} detik, maksimal {max_shots} segmen.\n"
        "Balas JSON persis berbentuk:\n"
        '{"shots":[{"play_start":0.0,"play_end":8.5,"pause_at":6.0,'
        '"narration":"...","reason":"lucu|kontroversial|edukatif|twist"}]}\n'
        "Aturan: pause_at berada di dalam [play_start, play_end]; jumlah durasi play "
        "+ estimasi baca narasi ≈ total budget.\n\nTRANSCRIP:\n" + transcript)


def _validate(data: dict, segments: list[dict], src_dur: float,
              budget: float) -> dict:
    shots = []
    total = 0.0
    for sh in data.get("shots", []):
        ps = float(sh["play_start"]); pe = float(sh["play_end"])
        pa = float(sh.get("pause_at", min(ps + 3, pe)))
        narration = str(sh.get("narration", "")).strip()
        if not narration or pe <= ps:
            continue
        if src_dur:
            ps = max(0.0, min(ps, src_dur)); pe = max(0.0, min(pe, src_dur))
        pa = max(ps, min(pa, pe))
        shots.append({"play_start": round(ps, 2), "play_end": round(pe, 2),
                      "pause_at": round(pa, 2), "narration": narration,
                      "reason": sh.get("reason", "reaksi")})
        total += (pe - ps)
        if total > budget * 1.3:
            break
    if not shots:
        raise llm.LLMError("Timeline LLM kosong/tidak valid")
    return {"shots": shots}


# ------------------------------------------------------------- heuristic fallback
HOOK_WORDS = ("gila", "astaga", "wow", "ternyata", "tahukah", "rahasia", "kontroversi",
              "lucu", "ngakak", "viral", "penting", "bahaya", "uang", "jt", "miliar",
              "jangan", "awas", "plot twist", "mindblown", "crazy", "insane", "secret")


def score_segment(text: str) -> float:
    t = text.lower()
    s = 0.0
    for w in HOOK_WORDS:
        if w in t:
            s += 2.0
    s += min(len(t) / 120.0, 1.5)                 # segmen informatif
    if "?" in t:
        s += 1.0
    if any(x in t for x in ("!", "haha", "wkwk", "😂")):
        s += 1.5
    if re.search(r"\d+(?:\.\d+)?\s*(%|jt|miliar|rb|ribu|juta)", t):
        s += 1.5
    return s


def heuristic_direct(segments: list[dict], budget: float, max_shots: int,
                     src_dur: float) -> dict:
    ranked = sorted(range(len(segments)),
                    key=lambda i: score_segment(segments[i]["text"]), reverse=True)
    picked = []
    used = 0.0
    span = 4.0
    for i in ranked:
        s = segments[i]
        if any(abs(s["start"] - p["play_start"]) < 2 for p in picked):
            continue
        play_end = min(s["end"] + 1.0, (src_dur or play_guard(s)) )
        pause_at = max(s["start"], min(play_end - 0.5, s["start"] + max(1.0, (s["end"] - s["start"]) * 0.7)))
        est_read = len(s["text"].split()) / 2.7
        if used + (play_end - s["start"]) + est_read > budget and picked:
            continue
        picked.append({
            "play_start": round(s["start"], 2),
            "play_end": round(play_end, 2),
            "pause_at": round(pause_at, 2),
            "narration": s["text"],
            "reason": "heuristik",
        })
        used += (play_end - s["start"]) + est_read
        if len(picked) >= max_shots:
            break
    picked.sort(key=lambda x: x["play_start"])
    return {"shots": picked}


def play_guard(s):
    return s["end"] + 1.0
