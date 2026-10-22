"""Langkah 4 — Kurasi Cacat Otomatis (AI Smart Cleanup) → Edit Decision List.

1. Word-Level Timestamping: Whisper memetakan stempel waktu tiap kata rekaman,
   termasuk gumaman ("eh", "hmm") dan pengulangan kalimat (multiple takes).
2. The AI Editor: LLM membaca sentimen & kelancaran; membuang take gagap,
   memilih satu take paling artikulatif per narasi target.
3. Output: EDL = daftar potongan bersih [start,end] + penanda jeda senyap.
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from .config import Settings
from . import llm
from .transcribe import transcribe

FILLERS = {"eh", "euh", "hmm", "hm", "anu", "kayak", "kayaknya", "gitu", "ya",
           "iyah", "emm", "um", "uh", "hah", "the", "aaa", "ooh", "woi", "tuh"}
STUTTER_RE = re.compile(r"\b(\w+)[\s,]+(?:\1[\s,]*){1,}\w+", re.I)


def cleanup(settings: Settings, recording: Path, timeline: dict,
            log=print) -> dict:
    log("   🧮 Word-level timestamping (Whisper lokal)…")
    segs, words = transcribe(recording, settings.whisper_model)
    dirty_text = " ".join(s["text"] for s in segs)

    targets = [sh["narration"] for sh in timeline["shots"]]
    takes = _group_takes(segs, targets)          # kandidat per narasi target

    try:
        edl_shots = _ai_select(settings, takes, timeline, log)
        log(f"   ✂️  The AI Editor ({settings.active_provider()}) memilih take terbaik.")
    except llm.LLMError as e:
        log(f"   ⚠️  {e}\n   ↪️  Pemilihan take via heuristik kelancaran…")
        edl_shots = _heuristic_select(takes, timeline)

    edl_shots = _trim_silence(edl_shots, segs, words)
    edl = {"recording": str(recording), "shots": edl_shots,
           "dirty_words": len(words), "removed_fillers": _count_fillers(words)}
    return edl


# ------------------------------------------------------------------ grouping takes
def _similarity(a: str, b: str) -> float:
    na = re.sub(r"[^\w\s]", "", a.lower())
    nb = re.sub(r"[^\w\s]", "", b.lower())
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _group_takes(segs: list[dict], targets: list[str]) -> list[dict]:
    """Kelompokkan segmen rekaman menjadi TAKE per narasi target.

    Sebuah take = rentang segmen berurutan; pemisah antar-take adalah gap senyap
    > 0.8 detik ATAU segmen yang tidak mirip sama sekali dengan naskah target.
    """
    groups = []
    for ti, tgt in enumerate(targets):
        takes = []
        for i, s in enumerate(segs):
            r = _similarity(s["text"], tgt)
            if r <= 0.42:
                continue
            prev = [t for t in takes if t["end_idx"] == i - 1]
            if prev:
                t = prev[0]
                t["end_idx"] = i
                t["end"] = s["end"]
                t["seg_indices"].append(i)
                t["score"] = (t["score"] + r) / 2
            else:
                takes.append({"shot": ti, "start": s["start"], "end": s["end"],
                              "start_idx": i, "end_idx": i, "score": r,
                              "seg_indices": [i]})
        # pisahkan take yang menyatu tapi mengandung jeda panjang di tengahnya
        split = []
        for t in takes:
            cur = {"shot": ti, "start": segs[t["seg_indices"][0]]["start"],
                   "end": segs[t["seg_indices"][0]]["end"],
                   "seg_indices": [t["seg_indices"][0]], "score": t["score"]}
            for a, b in zip(t["seg_indices"], t["seg_indices"][1:]):
                if segs[b]["start"] - segs[a]["end"] > 0.8:
                    cur["end"] = segs[a]["end"]
                    cur["text"] = " ".join(segs[j]["text"] for j in cur["seg_indices"])
                    split.append(cur)
                    cur = {"shot": ti, "start": segs[b]["start"], "end": segs[b]["end"],
                           "seg_indices": [b], "score": t["score"]}
                else:
                    cur["seg_indices"].append(b)
                    cur["end"] = segs[b]["end"]
            cur["text"] = " ".join(segs[j]["text"] for j in cur["seg_indices"])
            split.append(cur)
        groups.append({"target": tgt, "shot_index": ti, "takes": split})
    return groups


# ---------------------------------------------------------------------- AI select
SYSTEM_EDITOR = (
    "Kamu adalah editor AI untuk konten reaksi. Diberikan beberapa TAKE transkrip "
    "(hasil Whisper) yang mencoba mengucapkan satu kalimat target yang sama. "
    "Buang take yang gagap/repetitif/penuh filler; pilih SATU take paling artikulatif. "
    'Balas JSON: {"chosen_take": <index>, "confidence": 0-1, "reason": "..."}'
)


def _ai_select(settings: Settings, groups: list[dict], timeline: dict, log) -> list[dict]:
    out = []
    for g in groups:
        if not g["takes"]:
            continue
        if len(g["takes"]) == 1:
            t = g["takes"][0]
        else:
            payload = {
                "target_narration": g["target"],
                "takes": [{"index": i, "text": t["text"],
                           "filler_count": sum(1 for w in t["text"].lower().split()
                                               if w.strip(".,!?") in FILLERS),
                           "stutter": bool(STUTTER_RE.search(t["text"]))}
                          for i, t in enumerate(g["takes"])],
            }
            res = llm.ask_json(settings, SYSTEM_EDITOR, json.dumps(payload)[:6000])
            idx = int(res.get("chosen_take", 0)) % len(g["takes"])
            t = g["takes"][idx]
        sh = timeline["shots"][g["shot_index"]]
        out.append({"source_start": round(t["start"], 3),
                    "source_end": round(t["end"], 3),
                    "play_start": sh["play_start"], "play_end": sh["play_end"],
                    "pause_at": sh["pause_at"], "narration": sh["narration"]})
    if not out:
        raise llm.LLMError("Tidak ada take cocok dengan naskah (kemungkinan salah ucap total).")
    return out


# ---------------------------------------------------------------- heuristic select
def fluency_score(text: str) -> float:
    toks = text.lower().split()
    if not toks:
        return -1
    score = 10.0
    score -= sum(1 for w in toks if w.strip(".,!?") in FILLERS) * 0.7
    score -= len(STUTTER_RE.findall(text)) * 2.5
    uniq = len(set(toks)) / len(toks)
    score += uniq * 4
    return score


def _heuristic_select(groups: list[dict], timeline: dict) -> list[dict]:
    out = []
    for g in groups:
        if not g["takes"]:
            continue
        best = max(g["takes"], key=lambda t: t["score"] * 3 + (t["end"] - t["start"]))
        sh = timeline["shots"][g["shot_index"]]
        out.append({"source_start": round(best["start"], 3),
                    "source_end": round(best["end"], 3),
                    "play_start": sh["play_start"], "play_end": sh["play_end"],
                    "pause_at": sh["pause_at"], "narration": sh["narration"]})
    return out


# ------------------------------------------------------------- trim dead silence
def _trim_silence(shots: list[dict], segs: list[dict], words: list[dict]) -> list[dict]:
    """Potong hening >0.35s di kepala/ekor tiap take memakai stempel kata."""
    for sh in shots:
        ws = [w for w in words if w["start"] >= sh["source_start"] - 0.05
              and w["end"] <= sh["source_end"] + 0.05]
        if ws:
            sh["source_start"] = round(max(sh["source_start"], ws[0]["start"] - 0.15), 3)
            sh["source_end"] = round(min(sh["source_end"], ws[-1]["end"] + 0.20), 3)
    return shots


def _count_fillers(words: list[dict]) -> int:
    return sum(1 for w in words if w["word"].lower().strip(".,!?") in FILLERS)
