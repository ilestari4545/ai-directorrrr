"""Langkah 6 — Compositing, Auto-Audio Ducking & Render Final (FFmpeg).

Backend membaca EDL utuh: membuang bagian cacat, merangkai take bersih,
menempatkan efek/SFX/tipografi pada stempel waktu milidetik, melakukan ducking
volume video target (15% saat bicara, 100% saat diam), lalu mem-bake semuanya
menjadi satu MP4 memakai hardware encoder (NVENC/QSV/AMF) bila tersedia.

Struktur kanvas vertikal 1080x1920:
  - split_v : video target di atas, wajah reaksimu di bawah
  - pip     : video target penuh, wajah dalam lingkaran kecil di bawah
Semua efek divisualisasikan pada lapisan wajah (snap zoom, shake, B&W, dsb.)
dan SFX digenerate prosedural (tanpa aset eksternal) sehingga tetap jalan
di mesin mana pun.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


# ------------------------------------------------------------------- environment
def ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def probe_duration(path: str | Path) -> float:
    out = subprocess.run(
        [ffmpeg_bin(), "-i", str(path)], capture_output=True, text=True).stderr
    import re
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", out)
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def pick_encoder(preference: str = "auto") -> tuple[str, list[str]]:
    """Deteksi hw encoding (NVENC/QSV/AMF) lewat ffmpeg -encoders."""
    enc = ffmpeg_bin()
    try:
        listing = subprocess.run([enc, "-hide_banner", "-encoders"],
                                 capture_output=True, text=True, timeout=30).stdout
    except Exception:
        listing = ""
    candidates = {
        "nvenc": ("h264_nvenc", ["-preset", "p5", "-rc", "vbr", "-cq", "23",
                                 "-b:v", "8M"]),
        "qsv":   ("h264_qsv", ["-preset", "veryfast", "-global_quality", "23"]),
        "amf":   ("h264_amf", ["-quality", "balanced", "-rc", "cqp", "-qp_i", "22",
                               "-qp_p", "24"]),
    }
    order = ([preference] if preference in candidates else
             ["nvenc", "qsv", "amf"])
    for key in order:
        name, opts = candidates[key]
        if name in listing:
            return name, opts
    return "libx264", ["-preset", "veryfast", "-crf", "20"]


# ------------------------------------------------------------------ SFX generator
def gen_sfx(out_dir: Path, log=print) -> dict:
    """Generate seluruh SFX prosedural via ffmpeg lavfi (sine/noise) sekali saja."""
    out_dir.mkdir(parents=True, exist_ok=True)
    defs = {
        # nama: (durasi, filtergraph audio)
        "whoosh":        (0.35, "anoisesrc=d=0.35:c=pink:a=0.5,afade=t=in:st=0:d=0.12,afade=t=out:st=0.15:d=0.2"),
        "cha_ching":     (0.7,  "sine=frequency=1244:duration=0.7,sine=frequency=1864:duration=0.7,amix=inputs=2,tremolo=f=9,d=0.7,afade=t=out:st=0.4:d=0.3"),
        "bass_drop":     (1.2,  "sine=frequency=110:duration=1.2,vibrato=f=6:d=0.9,atempo=0.5,afade=t=out:st=0.7:d=0.5,aformat=sample_rates=44100"),
        "record_scratch":(0.6,  "anoisesrc=d=0.6:c=white:a=0.7,vibrato=f=25:d=1,afade=t=in:st=0:d=0.02,afade=t=out:st=0.35:d=0.25"),
        "crickets":      (2.0,  "sine=frequency=4200:duration=2.0,tremolo=f=22:d=0.9,volume=0.25,afade=t=in:st=0:d=0.2,afade=t=out:st=1.6:d=0.4"),
        "beep":          (0.5,  "sine=frequency=1000:duration=0.5,volume=0.6"),
        "whip_pan":      (0.3,  "anoisesrc=d=0.3:c=white:a=0.6,afade=t=in:st=0:d=0.05,afade=t=out:st=0.12:d=0.18"),
        "glitch":        (0.4,  "sine=frequency=220:duration=0.4,sine=frequency=177:duration=0.4,amix=inputs=2,vibrato=f=40:d=1,volume=0.5"),
        "rewind":        (1.0,  "sine=frequency=600:duration=1.0,vibrato=f=12:d=0.8,atempo=2.0,volume=0.4"),
        "shake_thump":   (0.8,  "sine=frequency=60:duration=0.8,volume=0.9,afade=t=out:st=0.4:d=0.4"),
    }
    paths = {}
    for name, (dur, fg) in defs.items():
        p = out_dir / f"sfx_{name}.wav"
        if not p.exists():
            subprocess.run([ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
                            "-f", "lavfi", "-i", fg, "-ar", "44100", "-ac", "2", str(p)],
                           check=True)
        paths[name] = p
    log(f"   🔊 {len(paths)} SFX prosedural siap.")
    return paths


# ------------------------------------------------------------------ FX → filter
SFX_ALIAS = {"cha_ching": "cha_ching", "bass_drop": "bass_drop",
             "record_scratch": "record_scratch", "crickets": "crickets",
             "whoosh": "whoosh", "whip_pan": "whip_pan", "hard_glitch": "glitch",
             "vhs_rewind": "rewind", "earthquake_shake": "shake_thump"}

VISUAL_FX = {"snap_zoom", "slow_push_in", "earthquake_shake", "desaturate",
             "kinetic_typography", "sentiment_emoji", "hand_marker", "auto_censor",
             "hard_glitch", "vhs_rewind", "whip_pan", "light_leak"}


def _esc(text: str) -> str:
    return (text.replace("\\", "\\\\").replace("'", "\\'")
            .replace(":", "\\:").replace("%", "\\%"))


def face_video_chain(sh: dict, fx_list: list[dict], idx: int, W: int, H: int) -> str:
    """Bangun filter chain untuk lapisan wajah satu shot (W x H region wajah)."""
    dur = sh["source_end"] - sh["source_start"]
    parts = []
    has_zoom = any(e["type"] == "snap_zoom" for e in fx_list)
    push = next((e for e in fx_list if e["type"] == "slow_push_in"), None)
    desat = next((e for e in fx_list if e["type"] == "desaturate"), None)
    shake = next((e for e in fx_list if e["type"] == "earthquake_shake"), None)
    glitch = next((e for e in fx_list if e["type"] == "hard_glitch"), None)

    # zoom-in temporal (zoompan) — snap_zoom 150% atau slow push-in
    zexpr = None
    if has_zoom:
        e = next(x for x in fx_list if x["type"] == "snap_zoom")
        t0, t1 = e["t_rel"], e["t_rel"] + e["dur"]
        sc = float(e["params"].get("scale", 1.5))
        zexpr = (f"if(lt(mod(T,{dur:.3f}),{t0:.3f}),1,"
                 f"if(lt(mod(T,{dur:.3f}),{t1:.3f}),{sc},"
                 f"if(lt(mod(T,{dur:.3f}),{t1+0.25:.3f},1+({sc}-1)*(1-(mod(T,{dur:.3f})-{t1})/0.25)),1)))")
    elif push:
        to = float(push["params"].get("to", 1.12))
        prog = f"min(mod(T,{dur:.3f})/{max(push['dur'],0.5):.3f},1)"
        zexpr = f"1+({to}-1)*{prog}"
    parts.append(f"scale={W}:{H}")
    if zexpr:
        parts.append(f"zoompan=z='{zexpr}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                     f":s={W}x{H}:fps=30")

    if shake:
        amp = int(shake["params"].get("strength", 12))
        t0, t1 = shake["t_rel"], shake["t_rel"] + shake["dur"]
        env = f"between(t,{t0:.3f},{t1:.3f})"
        parts.append(
            f"crop=iw-{2*amp}:ih-{2*amp}:"
            f"x='{amp}+{amp}*sin(45*t)*{env}':"
            f"y='{amp}+{amp}*cos(37*t)*{env}',scale={W}:{H}")
    if desat:
        t0, t1 = desat["t_rel"], desat["t_rel"] + desat["dur"]
        parts.append(f"hue=s=if(between(t,{t0:.3f},{t1:.3f}),0,1)")
    if glitch:
        t0, t1 = glitch["t_rel"], glitch["t_rel"] + glitch["dur"]
        parts.append(f"hue=s=2:b=0.35:mix=0.8:enable='between(t,{t0:.3f},{t1:.3f})'")
        parts.append(f"noise=alls=60:allf=t+u:enable='between(t,{t0:.3f},{t1:.3f})'")
    return ",".join(parts)


def overlay_drawtext(sh: dict, fx_list: list[dict], label: str, W: int, H: int) -> list[str]:
    """Filter drawbox/drawtext per efek anotasi pada lapisan wajah."""
    filters = []
    font = "Arial"
    for e in fx_list:
        t0 = e["t_rel"]; t1 = t0 + e["dur"]
        en = f"enable='between(t,{t0:.3f},{t1:.3f})'"
        if e["type"] == "kinetic_typography":
            word = _esc(str(e["params"].get("word", ""))[:14])
            size = int(90 * float(e["params"].get("size", 1.5)))
            filters.append(
                f"drawtext=text='{word}'"
                f":fontcolor=yellow:fontsize={size}:borderw=6:bordercolor=black:"
                f"x=(w-text_w)/2:y=h*0.12:{en}")
        elif e["type"] == "sentiment_emoji":
            emo = str(e["params"].get("emoji", "💀"))
            filters.append(
                f"drawtext=text='{emo}':fontsize=110:x=w-text_w-40:y=40:{en}")
        elif e["type"] == "hand_marker":
            color = e["params"].get("color", "red")
            # lingkaran/panah merah mengitari subjek (elips via drawbox tebal)
            filters.append(
                f"drawbox=x=iw*0.18:y=ih*0.22:w=iw*0.64:h=ih*0.5:"
                f"color={color}:thickness=8:t=fill:{en}")
        elif e["type"] == "auto_censor":
            filters.append(
                f"boxblur=luma_radius=24:luma_power=2:"
                f"enable='between(t,{t0:.3f},{t1:.3f})'")
        elif e["type"] == "whip_pan":
            filters.append(
                f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.35:t=fill:{en}")
        elif e["type"] == "light_leak":
            filters.append(
                f"drawbox=x=0:y=0:w=iw:h=ih:color=orange@0.22:t=fill:{en}")
        elif e["type"] == "vhs_rewind":
            filters.append(
                f"drawtext=text='<< REWIND':fontcolor=white:fontsize=64:"
                f"x=40:y=h-120:{en}")
    return filters


# --------------------------------------------------------------------- composer
def render(settings, edl: dict, timeline: dict, out_path: Path,
           workdir: Path, log=print) -> Path:
    ff = ffmpeg_bin()
    sfx = gen_sfx(workdir / "sfx", log)
    src = timeline["source_video"]
    rec = edl["recording"]
    OUT_W, OUT_H = 1080, 1920
    preset = settings.preset
    if preset == "pip":
        FW, FH, FY = 360, 640, OUT_H - FH - 40
        TW, TH, TY = OUT_W, OUT_H, 0
    else:  # split_v / fullscreen_target
        FW, FH, FY = OUT_W, 720, OUT_H - FH
        TW, TH, TY = OUT_W, OUT_H - FH, 0

    inputs, fc = [], []
    n = len(edl["shots"])
    # --- input: sumber berulang (play A, play B, wajah i) + sfx unik
    clip_plays = []
    for i, sh in enumerate(edl["shots"]):
        clip_plays.append(("playA", sh["play_start"], sh["pause_at"]))
        clip_plays.append(("playB", sh["pause_at"], sh["play_end"]))
        clip_plays.append(("face", sh["source_start"], sh["source_end"], i))
    in_map = {}
    idx = 0
    for item in clip_plays:
        kind = item[0]
        if kind == "face":
            inputs += ["-ss", f"{item[1]:.3f}", "-to", f"{item[2]:.3f}", "-i", rec]
            in_map[("face", item[3])] = idx
        else:
            inputs += ["-ss", f"{item[1]:.3f}", "-to", f"{item[2]:.3f}", "-i", src]
            in_map[(kind, item[1], item[2])] = idx
        idx += 1
    sfx_order = sorted(set(SFX_ALIAS.get(e["type"], "") for e in edl.get("effects", [])
                           if SFX_ALIAS.get(e["type"], "")))
    sfx_base = idx
    for name in sfx_order:
        inputs += ["-stream_loop", "-1", "-i", str(sfx[name])]
        idx += 1

    # --- hitung offset timeline final
    offsets, t = [], 0.0
    for sh in edl["shots"]:
        a = sh["pause_at"] - sh["play_start"]
        b = max(sh["play_end"] - sh["pause_at"], 0.05)
        f = sh["source_end"] - sh["source_start"]
        segA, segB = round(a, 3), round(min(b, 3.0), 3)
        offsets.append({"A": round(t, 3), "F": round(t + segA, 3),
                        "B": round(t + segA + f, 3),
                        "segA": segA, "segF": round(f, 3), "segB": segB})
        t += segA + f + segB
    total = round(t, 3)

    # --- filter graph
    for i, sh in enumerate(edl["shots"]):
        ia = in_map[("playA", sh["play_start"], sh["pause_at"])]
        ib = in_map[("playB", sh["pause_at"], sh["play_end"])]
        if_ = in_map[("face", i)]
        fc.append(f"[{ia}:v]scale={TW}:{TH}:force_original_aspect_ratio=decrease,"
                  f"pad={TW}:{TH}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[vta{i}]")
        fc.append(f"[{ib}:v]scale={TW}:{TH}:force_original_aspect_ratio=decrease,"
                  f"pad={TW}:{TH}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[vtb{i}]")
        fx_here = [e for e in edl.get("effects", []) if e["shot"] == i
                   and e["type"] in VISUAL_FX]
        chain = face_video_chain(sh, fx_here, i, FW, FH)
        dt = overlay_drawtext(sh, fx_here, "", FW, FH)
        fc.append(f"[{if_}:v]{chain}{(',' + ','.join(dt)) if dt else ''},setsar=1[f{i}]")
        fc.append(f"[{if_}:a]aresample=async=1[a{i}]")
        # ducking: target 100% saat A/B, 15% selama wajah bicara
        fc.append(f"[{ia}:a]aresample=async=1,apad=whole_dur={offsets[i]['segA']},"
                  f"aformat=sample_rates=44100:channel_layouts=stereo[sga{i}]")
        fc.append(f"[{ib}:a]atrim=0:{offsets[i]['segB']},asetpts=N/SR/TB,"
                  f"apad=whole_dur={offsets[i]['segB']},"
                  f"aformat=sample_rates=44100:channel_layouts=stereo[sgb{i}]")
        fc.append(f"[a{i}]volume=1.0,apad=whole_dur={offsets[i]['segF']},"
                  f"aformat=sample_rates=44100:channel_layouts=stereo[sgf{i}]")

    # concat audio segmental (jalur suara reaksimu)
    a_labels = []
    for i in range(n):
        fc.append(f"[sga{i}][sgf{i}][sgb{i}]concat=n=3:v=0:a=1[cat{i}]")
        a_labels.append(f"[cat{i}]")
    fc.append("".join(a_labels) + f"concat=n={n}:v=0:a=1[mixvoice]")

    # video: canvas hitam, tiap klip di-overlay tepat pada stempel waktunya
    fc.append(f"color=c=black:s={OUT_W}x{OUT_H}:r=30:d={total}[base]")
    cur = "[base]"
    for i, sh in enumerate(edl["shots"]):
        o = offsets[i]
        fc.append(f"[vta{i}]setpts=PTS-STARTPTS+{o['A']}/TB[va{i}]")
        fc.append(f"[vtb{i}]setpts=PTS-STARTPTS+{o['B']}/TB[vb{i}]")
        fc.append(f"[f{i}]setpts=PTS-STARTPTS+{o['F']}/TB[vc{i}]")
        fc.append(f"{cur}[va{i}]overlay=0:{TY}:enable='between(t,{o['A']},{o['A']+o['segA']})'[c{i}a]")
        fc.append(f"[c{i}a][vb{i}]overlay=0:{TY}:enable='between(t,{o['B']},{o['B']+o['segB']})'[c{i}b]")
        fc.append(f"[c{i}b][vc{i}]overlay=0:{FY}[c{i}c]")
        cur = f"[c{i}c]"
    fc.append(f"{cur}null[outv]")

    # audio mix: voice + target(ducked) + sfx
    # ducking envelope: volume 1.0 kecuali selama jendela wajah → 0.15
    duck_expr = "+".join(f"between(t,{o['F']:.3f},{o['F']+o['segF']:.3f})" for o in offsets)
    fc.append(f"anullsrc=r=44100:cl=stereo:d={total}[sil]")
    # target audio segments dirangkai mengikuti posisi A/B mereka:
    ta = []
    for i in range(n):
        o = offsets[i]
        fc.append(f"[sga{i}]atrim=0:{o['segA']}[t{i}a]")
        fc.append(f"[sgb{i}]atrim=0:{o['segB']}[t{i}b]")
        ta += [f"[t{i}a]", f"[t{i}b]"]
    fc.append("[sil][t0a]" + "".join(f"[t{i}b][t{i+1}a]" for i in range(n - 1)) +
              f"[t{n-1}b]concat=n={2*n+1}:v=0:a=1[tgtfull]")
    fc.append(f"[tgtfull]volume='if(gt({duck_expr},0),0.15,1.0)':eval=frame[duck]")
    mix_in = ["[mixvoice]", "[duck]"]
    amix_inputs = 2
    for name in sfx_order:
        si = sfx_base + sfx_order.index(name)
        # tempel tiap kemunculan sfx pada waktunya
        occ = [e for e in edl.get("effects", [])
               if SFX_ALIAS.get(e["type"]) == name]
        if not occ:
            continue
        lbls = []
        for j, e in enumerate(occ):
            start = offsets[e["shot"]]["A"] + e["t_rel"]
            if e["type"] in ("earthquake_shake",):
                start = offsets[e["shot"]]["F"] + e["t_rel"]
            if e["type"] in ("whip_pan", "hard_glitch", "vhs_rewind"):
                start = offsets[e["shot"]]["A"] + e["t_rel"]
            if e["type"] in ("cha_ching", "bass_drop", "crickets", "record_scratch",
                             "kinetic_typography"):
                start = offsets[e["shot"]]["F"] + e["t_rel"]
            if e["type"] == "auto_censor":
                start = offsets[e["shot"]]["F"] + e["t_rel"]
            fc.append(f"[{si}:a]atrim=0:{e['dur']:.3f},asetpts=N/SR/TB,"
                      f"adelay={int(start*1000)}|{int(start*1000)},"
                      f"apad=whole_dur={total}[sfx{name}{j}]")
            lbls.append(f"[sfx{name}{j}]")
        if lbls:
            fc.append("".join(lbls) + f"amix=inputs={len(lbls)}:normalize=0[sfxm{name}]")
            mix_in.append(f"[sfxm{name}]")
            amix_inputs += 1
    fc.append("".join(mix_in) + f"amix=inputs={amix_inputs}:duration=first:dropout_transition=0,"
              f"dynaudnorm=f=200[outa]")

    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error"] + inputs + \
          ["-filter_complex", ";".join(fc), "-map", "[outv]", "-map", "[outa]",
           "-t", str(total), "-r", "30"]
    codec, copts = pick_encoder(settings.hw_encoder)
    cmd += ["-c:v", codec] + copts + ["-c:a", "aac", "-b:a", "192k", str(out_path)]

    (workdir / "render_filtergraph.txt").write_text(";".join(fc), encoding="utf-8")
    log(f"   ⚙️  Encoder: {codec} | kanvas {OUT_W}x{OUT_H} | preset {preset} "
        f"| durasi final ≈ {total:.1f}s")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("FFmpeg gagal render:\n" + r.stderr[-2500:])
    log(f"   🎬 VIDEO FINAL: {out_path}")
    return out_path
