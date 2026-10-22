"""Langkah 3 — Mode Perekaman Terpandu (Smart Teleprompter).

Layar terbagi: preview video target + teleprompter naskah. Sistem yang memutar
video; saat tiba waktunya bereaksi, video DIJEDA otomatis oleh sistem, narasi
muncul di teleprompter, dan kreator membacanya ke webcam. Sinkronisasi terkunci.

Kontrol: [SPASI] play/pause manual · [→] lanjut paksa · [Q] berhenti · [R] ulang segmen
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path


class Recorder:
    """Player ffmpeg (pipe rawvideo) + teleprompter Tkinter, satu proses UI."""

    def __init__(self, timeline: dict, out_path: Path, log=print):
        self.tl = timeline
        self.shots = timeline["shots"]
        self.video = timeline["source_video"]
        self.out_path = Path(out_path)
        self.log = log
        self.W, self.H, self.FPS = 480, 852, 15   # kanvas preview vertikal

    # ------------------------------------------------------------------ playback
    def _spawn(self, start: float, end: float):
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
               "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", self.video,
               "-vf", f"scale={self.W}:{self.H}:force_original_aspect_ratio=decrease,"
                      f"pad={self.W}:{self.H}:(ow-iw)/2:(oh-ih)/2:black",
               "-r", str(self.FPS), "-pix_fmt", "bgr24", "-f", "rawvideo", "-"]
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def run(self) -> bool:
        try:
            import tkinter as tk
        except ImportError:
            self.log("   ❌ Tkinter tidak tersedia → rekaman terpandu butuh GUI.")
            return False
        return self._run_tk(tk)

    def _run_tk(self, tk) -> bool:
        root = tk.Tk()
        root.title("AI Auto-Director — Mode Perekaman Terpandu")
        root.configure(bg="#0b0b0f")

        from PIL import Image, ImageTk
        frame_b64 = {}
        canvas = tk.Canvas(root, width=self.W, height=self.H, bg="#000",
                           highlightthickness=0)
        canvas.pack(side="left")
        prompt = tk.Label(root, text="", font=("Segoe UI", 20, "bold"),
                          wraplength=520, justify="left", bg="#0b0b0f", fg="#ffd75e",
                          padx=24, pady=24)
        prompt.pack(side="left", fill="y", expand=True)
        status = tk.Label(root, text="", font=("Consolas", 11), bg="#0b0b0f", fg="#7ee787")
        status.place(x=10, rely=0.97, anchor="w")

        state = {"proc": None, "shot_i": 0, "phase": "idle",
                 "recording": False, "t_pause_start": 0.0, "done": False}

        # --- mulai perekaman layar+audio (Windows: Xbox Game Bar / OBS CLI bila ada)
        def start_capture():
            state["recording"] = True
            self.log("   🔴 REKAM dimulai — rekam seluruh jendela ini dengan "
                     "OBS/Game Bar (Win+G), lalu tekan SPASI untuk memutar video.")

        def spawn_next():
            stop_proc()
            if state["shot_i"] >= len(self.shots):
                finish()
                return
            sh = self.shots[state["shot_i"]]
            state["proc"] = self._spawn(sh["play_start"], sh["pause_at"])
            state["phase"] = "play"
            status.config(text=f"▶ SEG {state['shot_i']+1}/{len(self.shots)} — "
                               "video diputar OTOMATIS oleh sistem")

        def stop_proc():
            p = state["proc"]
            if p:
                try:
                    p.terminate(); p.wait(timeout=2)
                except Exception:
                    pass
            state["proc"] = None

        def on_pause():
            sh = self.shots[state["shot_i"]]
            stop_proc()
            state["phase"] = "paused"
            state["t_pause_start"] = time.time()
            prompt.config(text=f"⏸ VIDEO DIJEDA\n\n{sh['narration']}\n\n"
                               "🎤 Bacakan narasi ini sekarang…")
            status.config(text="⏸ AUTO-PAUSE — bacakan narasi di teleprompter "
                               "(→ untuk lanjut)")

        def next_shot():
            state["shot_i"] += 1
            prompt.config(text="")
            spawn_next()

        def finish():
            state["done"] = True
            stop_proc()
            prompt.config(text="✅ SELESAI.\nSimpan rekamanmu sebagai file video,\n"
                               "lalu lanjut ke tahap AI Smart Cleanup.")
            root.after(300, root.destroy)

        def tick():
            p = state["proc"]
            if p and state["phase"] == "play" and p.stdout:
                size = self.W * self.H * 3
                buf = p.stdout.read(size)
                if len(buf) == size:
                    import numpy as np
                    img = Image.fromarray(
                        np.frombuffer(buf, dtype=np.uint8).reshape(self.H, self.W, 3))
                    frame_b64["f"] = ImageTk.PhotoImage(img)
                    canvas.delete("all")
                    canvas.create_image(0, 0, image=frame_b64["f"], anchor="nw")
                else:
                    on_pause()
            root.after(16, tick)

        def key(e):
            k = e.keysym.lower()
            if k == "space":
                if state["phase"] == "idle":
                    start_capture(); spawn_next()
                elif state["phase"] == "paused":
                    next_shot()
            elif k == "right" and state["phase"] == "paused":
                next_shot()
            elif k == "r":
                spawn_next()
            elif k == "q":
                finish()

        root.bind("<Key>", key)
        prompt.config(text="🎬 MODE PEREKAMAN TERPANDU\n\n"
                           "1. Mulai rekam layar (Win+G / OBS).\n"
                           "2. Tekan SPASI — sistem memutar video & menjeda "
                           "otomatis sesuai JSON Timeline.\n\n[SPASI] mulai/jeda "
                           "· [→] lanjut · [R] ulang · [Q] selesai")
        tick()
        root.mainloop()

        if state["done"]:
            self.out_path.write_text(json.dumps(
                {"guided_session": True, "shots": len(self.shots)}, indent=2))
            self.log(f"   💾 Sesi terekam dicatat: {self.out_path.name}")
            return True
        return False


def record_webcam_ffmpeg(out_path: Path, seconds: int | None = None,
                         audio_dev: str = ":0", video_dev: str = "0", log=print) -> Path:
    """Alternatif tanpa-GUI: rekam webcam+mic langsung via dshow (Windows).

    ffmpeg -f dshow -i video="Cam":audio="Mic" ...
    """
    inp_v = f"video={video_dev}:audio={audio_dev}"
    cmd = ["ffmpeg", "-hide_banner", "-y", "-f", "dshow", "-i", inp_v]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += ["-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", str(out_path)]
    log(f"   🎥 Webcam recording → {out_path.name} (Ctrl+C untuk berhenti)")
    subprocess.run(cmd)
    return out_path
