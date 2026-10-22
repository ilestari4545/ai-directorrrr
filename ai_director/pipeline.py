"""AI Auto-Director Engine — Pipeline Orkestrator.

Menjalankan keenam langkah cetak biru secara berurutan:
  1 Inisialisasi & Ingesti   (yt-dlp + subtitle / Whisper darurat)
  2 Otak Penyutradaraan AI   (JSON Timeline Engine)
  3 Mode Perekaman Terpandu  (Smart Teleprompter, auto-pause otonom)
  4 Kurasi Cacat Otomatis    (Whisper word-level → AI Editor → EDL)
  5 Injeksi Efek Kontekstual (AI FX & Motion Engine → EDL+effects)
  6 Compositing & Render     (FFmpeg, ducking, NVENC/QSV)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .config import Settings
from . import ingest as ingest_mod
from . import transcribe as tr
from . import timeline as tl_mod
from . import cleanup as cl
from . import fx as fx_mod
from . import renderer


class Banner(list):
    def __call__(self, msg: str = "") -> None:
        print(msg, flush=True)
        self.append(msg)


def run_pipeline(url: str, recording: str | Path | None = None,
                 guided: bool = True, settings: Settings | None = None,
                 out: str | Path | None = None) -> dict:
    st = settings or Settings.load()
    log = Banner()
    t0 = time.time()
    result = {"steps": {}, "log": log}

    # ------------------------------------------------ 1. INISIALISASI & INGESTI
    log("\n" + "=" * 62)
    log("🎬 AI AUTO-DIRECTOR ENGINE")
    log("=" * 62)
    prov = st.active_provider()
    log(f"[1/6] Inisialisasi & Ingesti  | LLM: {prov or 'HEURISTIK LOKAL (tanpa API key)'}")
    probe = ingest_mod.ingest(url, Path("projects/_probe"), log=log) if url else None
    proj_dir = st.project_dir(probe["slug"]) if probe else st.project_dir("manual")
    project = probe or {}
    if not project.get("video"):
        raise SystemExit("Ingesti gagal: tidak ada video target.")
    result["steps"]["1_ingest"] = {"video": str(project["video"]),
                                   "subs": str(project["subs"]) if project.get("subs") else None}

    segments, words = tr.segments_from_source(project["video"], st, log=log)
    log(f"      {len(segments)} segmen transkrip, {len(words)} kata ber-stempel waktu.")

    # ------------------------------------------------ 2. OTAK PENYUTRADARAAN AI
    fmt_txt = "Shorts vertikal 60 detik" if st.target_format == "shorts" \
        else f"Video panjang {st.long_duration_s // 60} menit"
    log(f"\n[2/6] Otak Penyutradaraan AI  | format: {fmt_txt}")
    timeline = tl_mod.build_timeline(st, project, segments, log=log)
    for i, sh in enumerate(timeline["shots"]):
        log(f"      • SEG {i+1}: putar {sh['play_start']:.1f}-{sh['pause_at']:.1f}s "
            f"→ ⏸ JEDA → narasi: “{sh['narration'][:60]}…”")
    pdir = proj_dir
    (pdir / "timeline.json").write_text(json.dumps(timeline, indent=2, ensure_ascii=False),
                                        encoding="utf-8")
    result["steps"]["2_timeline"] = {"file": str(pdir / "timeline.json"),
                                     "shots": len(timeline["shots"])}

    # ---------------------------------------------- 3. MODE PEREKAMAN TERPANDU
    log("\n[3/6] Mode Perekaman Terpandu (Smart Teleprompter)")
    rec_path = Path(recording) if recording else None
    if guided and rec_path is None:
        from .recorder import Recorder
        ok = Recorder(timeline, pdir / "session.json", log=log).run()
        if not ok:
            log("      (Mode terpandu dilewati — jalankan dengan GUI, atau "
                "pasok --recording <file>.)")
    if rec_path is None:
        cand = sorted(pdir.glob("reaction*.*")) + sorted(pdir.glob("record*.*")) + \
               sorted(Path(".").glob("reaction*.mp4"))
        rec_path = cand[0] if cand else None
    if rec_path is None:
        log("      ⛔ Tidak ada file rekaman → pipeline berhenti di sini.\n"
            "         Rekam dulu (menu [3]) lalu jalankan lagi dengan --recording.")
        result["stopped_at"] = 3
        return result
    log(f"      Rekaman wajah/suara: {rec_path}")
    result["steps"]["3_record"] = {"recording": str(rec_path)}

    # ------------------------------------------------ 4. KURASI CACAT OTOMATIS
    log("\n[4/6] Kurasi Cacat Otomatis (AI Smart Cleanup)")
    edl = cl.cleanup(st, rec_path, timeline, log=log)
    log(f"      ✂️  EDL: {len(edl['shots'])} take bersih dipilih, "
        f"{edl['removed_fillers']} gumaman terdeteksi & terbuang.")
    (pdir / "edl.json").write_text(json.dumps(edl, indent=2, ensure_ascii=False),
                                   encoding="utf-8")
    result["steps"]["4_edl"] = {"file": str(pdir / "edl.json"),
                                "clean_shots": len(edl["shots"])}

    # word timestamps per shot untuk deteksi energi FX
    all_words = tr.transcribe(rec_path, st.whisper_model)[1]
    words_by_shot = [[w for w in all_words
                      if w["start"] >= s["source_start"] - .05 and w["end"] <= s["source_end"] + .05]
                     for s in edl["shots"]]

    # ------------------------------------------------ 5. INJEKSI EFEK KONTEKSTUAL
    log("\n[5/6] Injeksi Efek Kontekstual (AI FX & Motion Engine)")
    edl = fx_mod.inject_fx(st, edl, words_by_shot, log=log)
    kinds = {}
    for e in edl["effects"]:
        kinds[e["type"]] = kinds.get(e["type"], 0) + 1
    log("      🎇 " + ", ".join(f"{k}×{v}" for k, v in sorted(kinds.items())))
    (pdir / "edl_with_fx.json").write_text(json.dumps(edl, indent=2, ensure_ascii=False),
                                           encoding="utf-8")
    result["steps"]["5_fx"] = {"effects": len(edl["effects"]),
                               "kinds": kinds}

    # ------------------------------------------- 6. COMPOSITING & RENDER FINAL
    log("\n[6/6] Compositing, Auto-Audio Ducking & Render Final")
    final = Path(out) if out else pdir / "FINAL.mp4"
    renderer.render(st, edl, timeline, final, pdir / "render_tmp", log=log)
    result["steps"]["6_render"] = {"final": str(final)}
    result["output"] = str(final)
    result["elapsed"] = round(time.time() - t0, 1)
    log(f"\n✅ SELESAI dalam {result['elapsed']}s → {final}")
    log("   Video siap diunggah. Tinggal tekan upload.")
    return result
