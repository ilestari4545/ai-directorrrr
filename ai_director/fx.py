"""Langkah 5 — Injeksi Efek Kontekstual (AI FX & Motion Engine).

LLM (atau heuristik lokal) memindai take bersih untuk mendeteksi emosi, nada
suara, dan kata kunci, lalu menyisipkan instruksi efek ke dalam EDL:

  Visual : snap_zoom(150%), slow_push_in, earthquake_shake, desaturate(B&W+vignette)
  SFX    : bass_drop, record_scratch, crickets, cha_ching, whoosh
  Tipografi: kinetic_typography, hand_marker, sentiment_emoji, auto_censor(bip)
  Transisi : whip_pan, light_leak, vhs_rewind, hard_glitch

Setiap efek = {"t": detik-relatif-dalam-final, "dur": ..., "type": ..., "params": ...}
"""
from __future__ import annotations

import re

from .config import Settings
from . import llm

EMOJI_MAP = {
    "💀": ("ngakak", "mati", "gila", "absurd", "malu", "cringe", "wkwk", "dead"),
    "🔥": ("keren", "api", "hebat", "gokil", "fire", "best", "spektakuler", "viral"),
    "😱": ("kaget", "takut", "seram", "ngeri", "shock", "wah"),
    "🤯": ("mindblown", "plot twist", "ternyata", "tidak masuk akal", "gila banget"),
    "💸": ("rupiah", "juta", "miliar", "harga", "uang", "cuan", "rb", "jt"),
}
MONEY_RE = re.compile(r"(rp|rupiah|dolar|\$\s?\d|\b\d+[\.,]?\d*\s?(rb|ribu|jt|juta|miliar|k\b))", re.I)
PROFANITY = ("bangsat", "brengsek", "sialan", "anjing", "babi", "tai ", "kontol",
             "memek", "goblok", "bodoh amat", "fck", "shit", "damn", "asshole")

SYSTEM_FX = (
    "Kamu adalah motion-graphics director. Diberikan daftar SHOT reaksi bersih "
    "(teks + durasi + titik pause video target), tentukan efek kontekstual per shot. "
    "Pilih dari: snap_zoom, slow_push_in, earthquake_shake, desaturate, bass_drop, "
    "record_scratch, crickets, cha_ching, whoosh, kinetic_typography, hand_marker, "
    "sentiment_emoji, auto_censor, whip_pan, light_leak, vhs_rewind, hard_glitch. "
    'Balas JSON: {"effects":[{"shot":0,"type":"snap_zoom","at":0.5,"dur":0.8,'
    '"params":{}}]} — "at" relatif terhadap awal shot (detik).'
)


def inject_fx(settings: Settings, edl: dict, words_by_shot: list[list[dict]],
              log=print) -> dict:
    """words_by_shot: word timestamps rekaman per shot EDL (untuk energi/volume proxy)."""
    try:
        data = llm.ask_json(settings, SYSTEM_FX, _prompt(edl, words_by_shot))
        effects = _normalize(data.get("effects", []), edl)
        log(f"   🎇 AI FX Engine ({settings.active_provider()}): "
            f"{len(effects)} efek terinjeksi.")
    except llm.LLMError as e:
        log(f"   ⚠️  {e}\n   ↪️  Menyuntik efek via detektor heuristik lokal…")
        effects = heuristic_fx(edl, words_by_shot)
    edl["effects"] = effects
    return edl


# --------------------------------------------------------------------- LLM path
def _prompt(edl: dict, words_by_shot) -> str:
    shots = []
    for i, sh in enumerate(edl["shots"]):
        d = round(sh["source_end"] - sh["source_start"], 2)
        shots.append({"shot": i, "narration": sh["narration"], "dur": d,
                      "target_pause_at": sh["pause_at"]})
    import json
    return json.dumps({"total_duration_est": sum(s["dur"] for s in shots)}, ensure_ascii=False) \
        + "\nSHOTS:\n" + json.dumps(shots, ensure_ascii=False)[:7000]


def _normalize(effects: list, edl: dict) -> list:
    allowed = set(SYSTEM_FX.split("Pilih dari:")[1].split("Balas")[0].replace(".", "").split(", "))
    out = []
    n = len(edl["shots"])
    for e in effects:
        t = str(e.get("type", ""))
        if t not in allowed:
            continue
        si = int(e.get("shot", 0))
        if not (0 <= si < n):
            continue
        sh = edl["shots"][si]
        at = max(0.0, float(e.get("at", 0)))
        dur = max(0.2, float(e.get("dur", 0.6)))
        out.append({"shot": si, "t_rel": round(min(at, sh["source_end"] - sh["source_start"]), 3),
                    "dur": round(dur, 3), "type": t, "params": e.get("params", {})})
    return out


# ------------------------------------------------------------- heuristic fallback
def shot_energy(words: list[dict]) -> tuple[float, float]:
    """(rata-rata kata/detik, rasio kata kapital/panjang) sebagai proksi volume/nada."""
    if not words:
        return 0.0, 0.0
    span = max((words[-1]["end"] - words[0]["start"]), 0.1)
    cps = len(words) / span
    caps = sum(1 for w in words if w["word"].isupper() and len(w["word"]) > 1)
    return cps, caps / max(len(words), 1)


def find_word(words: list[dict], shot_start: float, needle: str) -> float | None:
    """Posisi (t_rel) kemunculan kata pertama yang memuat `needle`."""
    n = needle.lower().strip("!?.,;")
    for w in words:
        if n and n in re.sub(r"[^\w]", "", w["word"].lower()):
            return max(0.0, w["start"] - shot_start)
    return None


def heuristic_fx(edl: dict, words_by_shot: list[list[dict]]) -> list[dict]:
    fx: list[dict] = []

    def add(si, typ, at, dur=0.6, **params):
        sh = edl["shots"][si]
        span = sh["source_end"] - sh["source_start"]
        fx.append({"shot": si, "t_rel": round(max(0.0, min(at, span)), 3),
                   "dur": dur, "type": typ, "params": params})

    prev_topic_words = None
    for si, sh in enumerate(edl["shots"]):
        text = sh["narration"]
        low = text.lower()
        words = words_by_shot[si] if si < len(words_by_shot) else []
        base = 0.0  # t_rel dihitung dari awal shot
        cps, caps_ratio = shot_energy(words)

        # --- manipulasi visual
        if any(k in low for k in ("gila", "astaga", "wow", "hah", "apa", "kaget", "serius?")) or caps_ratio > 0.25:
            add(si, "snap_zoom", base + 0.2, 0.9, scale=1.5)
        elif any(k in low for k in ("yang sebenarnya", "artinya", "kesimpulan", "namun", "sayangnya", "dalam")):
            add(si, "slow_push_in", base, round(sh["source_end"] - sh["source_start"], 2), to=1.12)
        if any(k in low for k in ("ngakak", "wkwk", "hafaha", "ketawa", "😂")):
            add(si, "earthquake_shake", base + 0.3, 0.8, strength=14)
        if any(k in low for k in ("hela napas", "pasrah", "putus asa", "ya sudah", "deadpan", "diam")):
            add(si, "desaturate", base + 0.4, 1.6, vignette=0.55)

        # --- audio SFX
        if MONEY_RE.search(low):
            wpos = find_word(words, sh["source_start"], "rp") \
                or find_word(words, sh["source_start"], "juta") \
                or find_word(words, sh["source_start"], "jt") or base + 0.3
            add(si, "cha_ching", wpos, 0.5)
        if any(k in low for k in ("tunggu dulu", "sebentar", "eh eh", "stop")):
            add(si, "record_scratch", base + 0.15, 0.6)
        if cps < 1.6 and (sh["source_end"] - sh["source_start"]) > 2.5:
            add(si, "crickets", base + 0.6, 1.8)          # momen garing
        if any(k in low for k in ("ternyata", "tahukah", "fakta", "plot twist", "rahasia")) or cps > 4.2:
            add(si, "bass_drop", base + 0.4, 1.0)

        # --- anotasi & tipografi
        loud_words = [w for w in words
                      if len(w["word"]) >= 5 and w["word"].isalpha()]
        for w in loud_words[:3]:
            add(si, "kinetic_typography", w["start"] - sh["source_start"] + base,
                0.5, word=w["word"], size=1.6)
        for emo, keys in EMOJI_MAP.items():
            hit = next((k for k in sorted(keys, key=len, reverse=True) if k in low), None)
            if hit:
                wpos = find_word(words, sh["source_start"], hit.split()[0])
                add(si, "sentiment_emoji", wpos if wpos is not None else base + 0.5,
                    1.2, emoji=emo)
        bad = next((p for p in PROFANITY if p in low), None)
        if bad:
            wpos = find_word(words, sh["source_start"], bad.strip().split()[0])
            add(si, "auto_censor", wpos if wpos is not None else base + 1.0,
                0.8, blur="mouth", beep=True)
        objs = [o for o in re.findall(r"(?:^|\s)(ini|itu|dia)(?:$|\s)", low) if o]
        if objs:
            wpos = find_word(words, sh["source_start"], objs[0])
            add(si, "hand_marker", wpos if wpos is not None else base + 0.8,
                1.2, shape="circle", color="red")

        # --- transisi antar-shot
        topic = set(re.findall(r"\w+", low))
        if si > 0:
            gap = 0.0
            if prev_topic_words:
                jaccard = len(topic & prev_topic_words) / max(len(topic | prev_topic_words), 1)
                if jaccard < 0.15:
                    typ = "whip_pan" if caps_ratio > 0.1 else "light_leak"
                    # transisi menempel di AKHIR shot sebelumnya
                    prev = edl["shots"][si - 1]
                    at = round(prev["source_end"] - prev["source_start"] - 0.3, 3)
                    fx.append({"shot": si - 1, "t_rel": max(0.0, at), "dur": 0.3,
                               "type": typ, "params": {}})
            if "coba kita ulang" in low or "ulang bagian" in low:
                add(si, "vhs_rewind", base, 1.0)
            if any(k in low for k in ("eh tapi", "maksudku", "koreksi", "sebenarnya bukan")):
                add(si, "hard_glitch", base + 0.5, 0.5)
        prev_topic_words = topic

    # whoosh setiap elemen masuk frame (efek pertama tiap shot)
    seen = set()
    extra = []
    for e in fx:
        if e["shot"] not in seen:
            seen.add(e["shot"])
            extra.append({"shot": e["shot"], "t_rel": 0.05, "dur": 0.35,
                          "type": "whoosh", "params": {}})
    edl["effects"] = sorted(fx + extra, key=lambda x: (x["shot"], x["t_rel"]))
    return edl
