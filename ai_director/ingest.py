"""Langkah 1 — Ingesti: yt-dlp mengunduh video mentah resolusi tinggi + subtitle."""
from __future__ import annotations

import re
from pathlib import Path

import yt_dlp


def slugify(url: str, title: str = "") -> str:
    base = title or re.sub(r"[^a-z0-9]+", "-", url.lower())
    return re.sub(r"-+", "-", base).strip("-")[:60] or "project"


def ingest(url: str, out_dir: Path, log=print) -> dict:
    """Unduh video (prioritas vertikal bila tersedia, jika tidak kualitas tertinggi) + auto subs.

    Mengembalikan {"video": Path, "subs": Path|None, "title": str, "duration": float}
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    # sumber lokal (file:// atau path langsung) → salin, tak perlu yt-dlp
    local = None
    if url.startswith("file:"):
        local = Path(url[5:])
    elif Path(url).exists():
        local = Path(url)
    if local is not None and local.exists():
        dst = out_dir / local.name
        if not dst.exists():
            import shutil as _sh; _sh.copy(local, dst)
        dur = 0.0
        try:
            from .renderer import probe_duration
            dur = probe_duration(dst)
        except Exception:
            pass
        log(f"   ✅ (lokal) {dst.name} ({dur:.0f}s)")
        return {"video": dst, "subs": next((dst.with_suffix(e) for e in (".vtt", ".srt")
                                           if dst.with_suffix(e).exists()), None),
                "title": dst.stem, "duration": dur,
                "slug": slugify(str(local), dst.stem)}

    tpl = str(out_dir / "%(title)s.%(ext)s")

    ydl_opts = {
        # prioritas: vertikal (Shorts/TikTok) lalu kualitas tertinggi
        "format": "bv*[height<=1080][aspect_ratio=9/16]+ba/b[aspect_ratio=9/16]/"
                  "bv*+ba/best",
        "outtmpl": tpl,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["id", "en"],
        "subtitlesformat": "vtt/srt/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [lambda d: log(f"   ⬇️  {d.get('_percent_str','?')} "
                                         f"{d.get('_eta_str','')}")
                           if d.get("status") == "downloading" else None],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    title = info.get("title", "video")
    duration = float(info.get("duration") or 0)
    video_path = None
    for ext in ("mp4", "mkv", "webm"):
        p = out_dir / f"{title}.{ext}"
        if p.exists():
            video_path = p
            break
    if video_path is None:
        cands = sorted(out_dir.glob("*.mp4"), key=lambda p: p.stat().st_size, reverse=True)
        if not cands:
            raise RuntimeError("yt-dlp tidak menghasilkan file video.")
        video_path = cands[0]

    subs = None
    for ext in ("vtt", "srt"):
        p = video_path.with_suffix(f".{ext}")
        if p.exists() and p.stat().st_size > 50:
            subs = p
            break
        # pola yt-dlp: judul.lang.vtt
        alt = list(out_dir.glob(f"*.{ext}"))
        if alt:
            subs = alt[0].rename(p) if len(alt) == 1 else alt[0]
            break

    log(f"   ✅ Video: {video_path.name} ({duration:.0f}s) | Subtitle: "
        f"{subs.name if subs else 'TIDAK ADA → butuh Whisper'}")
    return {"video": video_path, "subs": subs, "title": title,
            "duration": duration, "slug": slugify(url, title)}
