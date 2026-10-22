#!/usr/bin/env python3
"""AI Auto-Director Engine — entry point CLI & GUI.

Contoh pemakaian:
  # jalankan seluruh pipeline dari tautan YouTube/TikTok (butuh rekaman)
  python main.py run --url "https://youtube.com/shorts/xxxx" --recording reaction.mp4

  # tanpa API key pun tetap jalan (fallback heuristik lokal)
  python main.py timeline --url "..."          # langkah 1-2 saja
  python main.py record  --project projects/.. # langkah 3 (teleprompter GUI)
  python main.py gui                           # aplikasi desktop (.exe membuka ini)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ai_director.config import Settings


def cmd_run(a):
    from ai_director.pipeline import run_pipeline
    st = Settings.load()
    if a.format:
        st.target_format = a.format
    if a.preset:
        st.preset = a.preset
    res = run_pipeline(a.url, recording=a.recording, guided=not a.no_guided,
                       settings=st, out=a.out)
    print(json.dumps({k: v for k, v in res.items() if k != "log"},
                     indent=2, ensure_ascii=False, default=str))


def cmd_timeline(a):
    from ai_director import ingest, transcribe, timeline as tl
    st = Settings.load()
    if a.format:
        st.target_format = a.format
    log = print
    proj = ingest.ingest(a.url, Path("projects/_probe"), log=log)
    pdir = st.project_dir(proj["slug"])
    segs, words = transcribe.segments_from_source(proj["video"], st, log=log)
    t = tl.build_timeline(st, proj, segs, log=log)
    out = pdir / "timeline.json"
    out.write_text(json.dumps(t, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"💾 {out} ({len(t['shots'])} segmen)")


def cmd_record(a):
    from ai_director.recorder import Recorder
    t = json.loads(Path(a.timeline).read_text(encoding="utf-8"))
    ok = Recorder(t, Path(a.timeline).with_name("session.json")).run()
    sys.exit(0 if ok else 1)


def cmd_cleanup(a):
    from ai_director import cleanup as cl
    st = Settings.load()
    t = json.loads(Path(a.timeline).read_text(encoding="utf-8"))
    edl = cl.cleanup(st, Path(a.recording), t)
    Path(a.out).write_text(json.dumps(edl, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"✂️ EDL → {a.out}: {len(edl['shots'])} take bersih, "
          f"{edl['removed_fillers']} gumaman terbuang")


def cmd_fx(a):
    from ai_director import fx as fxm, transcribe as tr
    st = Settings.load()
    edl = json.loads(Path(a.edl).read_text(encoding="utf-8"))
    words = tr.transcribe(edl["recording"], st.whisper_model)[1]
    wbs = [[w for w in words if w["start"] >= s["source_start"] - .05
            and w["end"] <= s["source_end"] + .05] for s in edl["shots"]]
    edl = fxm.inject_fx(st, edl, wbs)
    Path(a.out).write_text(json.dumps(edl, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"🎇 {len(edl['effects'])} efek → {a.out}")


def cmd_render(a):
    from ai_director import renderer
    st = Settings.load()
    if a.preset:
        st.preset = a.preset
    edl = json.loads(Path(a.edl).read_text(encoding="utf-8"))
    tlj = json.loads(Path(a.timeline).read_text(encoding="utf-8"))
    renderer.render(st, edl, tlj, Path(a.out), Path(a.out).parent / "render_tmp")
    print(f"🎬 {a.out}")


def cmd_keys(a):
    st = Settings.load()
    if a.set:
        prov, key = a.set.split("=", 1)
        st.set_key(prov, key)
        print(f"🔑 {prov} tersimpan.")
    else:
        active = st.active_provider()
        print("Provider aktif:", active or "(belum ada — mode heuristik lokal)")
        for p in list(st.api_keys):
            print(f"  {p}: ****{str(st.api_keys[p])[-4:]}")


def cmd_gui(_a):
    from ai_director.gui import main as gui_main
    gui_main()


def build_parser():
    ap = argparse.ArgumentParser(prog="ai-director",
                                 description="AI Auto-Director Engine")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="jalankan pipeline penuh 1→6")
    r.add_argument("--url", required=True)
    r.add_argument("--recording", help="file rekaman wajah/suara (langkah 3)")
    r.add_argument("--format", choices=["shorts", "long"])
    r.add_argument("--preset", choices=["split_v", "pip"])
    r.add_argument("--out")
    r.add_argument("--no-guided", action="store_true")
    r.set_defaults(fn=cmd_run)

    t = sub.add_parser("timeline", help="langkah 1-2: ingesti + JSON Timeline")
    t.add_argument("--url", required=True)
    t.add_argument("--format", choices=["shorts", "long"])
    t.set_defaults(fn=cmd_timeline)

    rc = sub.add_parser("record", help="langkah 3: teleprompter terpandu (GUI)")
    rc.add_argument("--timeline", required=True)
    rc.set_defaults(fn=cmd_record)

    c = sub.add_parser("cleanup", help="langkah 4: AI Smart Cleanup → EDL")
    c.add_argument("--recording", required=True)
    c.add_argument("--timeline", required=True)
    c.add_argument("--out", default="edl.json")
    c.set_defaults(fn=cmd_cleanup)

    f = sub.add_parser("fx", help="langkah 5: injeksi efek kontekstual ke EDL")
    f.add_argument("--edl", required=True)
    f.add_argument("--out", default="edl_with_fx.json")
    f.set_defaults(fn=cmd_fx)

    d = sub.add_parser("render", help="langkah 6: compositing + ducking + render")
    d.add_argument("--edl", required=True)
    d.add_argument("--timeline", required=True)
    d.add_argument("--preset", choices=["split_v", "pip"])
    d.add_argument("--out", default="FINAL.mp4")
    d.set_defaults(fn=cmd_render)

    k = sub.add_parser("keys", help="kelola multi-API key")
    k.add_argument("--set", metavar="PROVIDER=KEY")
    k.set_defaults(fn=cmd_keys)

    g = sub.add_parser("gui", help="aplikasi desktop lengkap")
    g.set_defaults(fn=cmd_gui)
    return ap


def main(argv=None):
    ap = build_parser()
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
