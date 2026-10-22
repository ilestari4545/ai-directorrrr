"""Aplikasi desktop AI Auto-Director Engine (Tkinter).

Inilah antarmuka .exe: satu jendela, enam tahap sesuai cetak biru, dengan log
langsung dan tombol "⚡ JALANKAN OTOMATIS" yang mengeksekusi pipeline penuh.
"""
from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .config import Settings, PROVIDERS


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.st = Settings.load()
        root.title("AI Auto-Director Engine v1.0")
        root.geometry("980x720")
        root.configure(bg="#101014")
        self.q: queue.Queue = queue.Queue()

        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TButton", font=("Segoe UI", 10))

        head = tk.Label(root, text="🎬 AI AUTO-DIRECTOR ENGINE",
                        font=("Segoe UI", 20, "bold"), bg="#101014", fg="#ffd75e")
        head.pack(pady=(14, 2))
        tk.Label(root, text="Sutradara + Operator Kamera + Editor — semuanya otomatis.",
                 font=("Segoe UI", 10), bg="#101014", fg="#9aa0a6").pack()

        # ------------------------------------------------ step 1 : ingesti
        f1 = ttk.LabelFrame(root, text="1 · Inisialisasi & Ingesti Data", padding=8)
        f1.pack(fill="x", padx=14, pady=6)
        tk.Label(f1, text="Tautan YouTube/TikTok:").grid(row=0, column=0, sticky="w")
        self.url = tk.Entry(f1, width=70, font=("Consolas", 10))
        self.url.grid(row=0, column=1, padx=6)
        self.video_manual = tk.StringVar()
        ttk.Button(f1, text="…video lokal", command=self.pick_video).grid(row=0, column=2)
        tk.Label(f1, text="Rekaman wajah (opsional):").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Button(f1, text="Pilih file rekaman", command=self.pick_rec).grid(row=1, column=1, sticky="w", padx=6)
        self.rec_label = tk.Label(f1, text="(belum dipilih — akan lewat mode terpandu)",
                                  fg="#9aa0a6", anchor="w")
        self.rec_label.grid(row=1, column=2, columnspan=2, sticky="w")

        # ------------------------------------------------ step 2 : sutradara
        f2 = ttk.LabelFrame(root, text="2 · Otak Penyutradaraan AI", padding=8)
        f2.pack(fill="x", padx=14, pady=6)
        self.fmt = tk.StringVar(value=self.st.target_format)
        ttk.Radiobutton(f2, text="YouTube Shorts vertikal 60 detik", variable=self.fmt,
                        value="shorts").grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(f2, text="Video Panjang 8 menit", variable=self.fmt,
                        value="long").grid(row=0, column=1, sticky="w")
        self.preset = tk.StringVar(value=self.st.preset)
        ttk.Combobox(f2, textvariable=self.preset, values=["split_v", "pip"],
                     width=10, state="readonly").grid(row=0, column=2, padx=8)
        ttk.Label(f2, text="preset visual").grid(row=0, column=3, sticky="w")
        ttk.Button(f2, text="Susun JSON Timeline", command=self.do_timeline).grid(row=1, column=0, pady=6, sticky="w")

        # ------------------------------------------------ step 3-6 aksi
        f3 = ttk.LabelFrame(root, text="3–6 · Rekaman Terpandu → Cleanup → FX → Render", padding=8)
        f3.pack(fill="x", padx=14, pady=6)
        grid = dict(padx=6, pady=4)
        ttk.Button(f3, text="🎤 3. Mode Teleprompter", command=self.do_record).grid(row=0, column=0, **grid)
        ttk.Button(f3, text="✂️ 4. AI Smart Cleanup", command=self.do_cleanup).grid(row=0, column=1, **grid)
        ttk.Button(f3, text="🎇 5. Injeksi Efek AI FX", command=self.do_fx).grid(row=0, column=2, **grid)
        ttk.Button(f3, text="🏁 6. Render Final", command=self.do_render).grid(row=0, column=3, **grid)
        big = tk.Button(f3, text="⚡ JALANKAN SEMUA OTOMATIS (1→6)",
                        font=("Segoe UI", 12, "bold"), bg="#ffd75e", fg="#101014",
                        relief="flat", padx=18, pady=8, command=self.do_all)
        big.grid(row=1, column=0, columnspan=4, pady=8, sticky="we")

        bar = ttk.Frame(root); bar.pack(fill="x", padx=14)
        self.prog = ttk.Progressbar(bar, mode="indeterminate")
        self.prog.pack(side="left", fill="x", expand=True, pady=6)
        ttk.Button(bar, text="🔑 API Keys", command=self.do_keys).pack(side="right", padx=4)
        ttk.Button(bar, text="Buka folder proyek", command=self.open_folder).pack(side="right")

        # ------------------------------------------------ log
        self.logbox = tk.Text(root, height=18, bg="#0b0b0f", fg="#d4d4d4",
                              font=("Consolas", 9), state="disabled")
        self.logbox.pack(fill="both", expand=True, padx=14, pady=(4, 12))
        self.root.after(120, self.poll)

    # ------------------------------------------------------------------ helpers
    def log(self, msg=""):
        print(msg, flush=True)
        self.q.put(str(msg))

    def poll(self):
        try:
            while True:
                m = self.q.get_nowait()
                self.logbox.configure(state="normal")
                self.logbox.insert("end", m + "\n")
                self.logbox.see("end")
                self.logbox.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(120, self.poll)

    def pick_video(self):
        p = filedialog.askopenfilename(filetypes=[("Video", "*.mp4 *.mkv *.webm *.mov")])
        if p:
            self.url.delete(0, "end"); self.url.insert(0, "file:" + p)

    def pick_rec(self):
        p = filedialog.askopenfilename(filetypes=[("Video rekaman", "*.mp4 *.mkv *.mov *.webm")])
        if p:
            self.rec_path = p
            self.rec_label.configure(text=p, fg="#7ee787")

    def _source(self):
        u = self.url.get().strip()
        if not u:
            messagebox.showwarning("AI Director", "Tempelkan tautan video target dulu.")
            return None
        return u

    def _settings(self):
        self.st.target_format = self.fmt.get()
        self.st.preset = self.preset.get()
        self.st.save()
        return self.st

    def worker(self, fn):
        def run():
            self.prog.start(12)
            try:
                fn()
            except Exception as e:
                self.log(f"❌ {e}")
            finally:
                self.root.after(0, self.prog.stop)
        threading.Thread(target=run, daemon=True).start()

    # ------------------------------------------------------------------ actions
    def do_timeline(self):
        url = self._source()
        if not url:
            return
        st = self._settings()

        def job():
            from ai_director import ingest, transcribe
            from ai_director.timeline import build_timeline
            self.log("[Langkah 1] Ingesti materi…")
            proj = ingest.ingest(url, Path("projects/_probe"), log=self.log)
            pdir = st.project_dir(proj["slug"]); self.pdir = pdir; self.proj = proj
            segs, words = transcribe.segments_from_source(proj["video"], st, log=self.log)
            self.log("[Langkah 2] Otak penyutradaraan AI menyusun timeline…")
            tl = build_timeline(st, proj, segs, log=self.log)
            (pdir / "timeline.json").write_text(json.dumps(tl, indent=2, ensure_ascii=False),
                                                encoding="utf-8")
            self.timeline_path = pdir / "timeline.json"
            self.log(f"✅ timeline.json tersimpan di {pdir} ({len(tl['shots'])} segmen)")
        self.worker(job)

    def do_record(self):
        tl = getattr(self, "timeline_path", None)
        if not tl or not Path(tl).exists():
            return self.log("⚠️ Susun JSON Timeline terlebih dahulu (Langkah 2).")

        def job():
            from ai_director.recorder import Recorder
            data = json.loads(Path(tl).read_text(encoding="utf-8"))
            ok = Recorder(data, Path(tl).with_name("session.json"), log=self.log).run()
            if ok:
                p = filedialog.askopenfilename(title="Pilih file hasil rekamanmu",
                                               filetypes=[("Video", "*.mp4 *.mkv *.mov")])
                if p:
                    self.rec_path = p
                    self.root.after(0, lambda: self.rec_label.configure(
                        text=p, fg="#7ee787"))
        self.worker(job)

    def do_cleanup(self):
        rec = getattr(self, "rec_path", None)
        tl = getattr(self, "timeline_path", None)
        if not (rec and tl):
            return self.log("⚠️ Butuh rekaman + timeline (langkah 1–3).")
        st = self._settings()

        def job():
            from ai_director.cleanup import cleanup
            t = json.loads(Path(tl).read_text(encoding="utf-8"))
            edl = cleanup(st, Path(rec), t, log=self.log)
            out = Path(tl).with_name("edl.json")
            out.write_text(json.dumps(edl, indent=2, ensure_ascii=False), encoding="utf-8")
            self.edl_path = out
            self.log(f"✂️ EDL bersih: {len(edl['shots'])} take terpilih → {out}")
        self.worker(job)

    def do_fx(self):
        edl = getattr(self, "edl_path", None)
        if not edl or not Path(edl).exists():
            return self.log("⚠️ Jalankan AI Smart Cleanup dulu (Langkah 4).")
        st = self._settings()

        def job():
            from ai_director.fx import inject_fx
            from ai_director.transcribe import transcribe
            d = json.loads(Path(edl).read_text(encoding="utf-8"))
            words = transcribe(d["recording"], st.whisper_model)[1]
            wbs = [[w for w in words if w["start"] >= s["source_start"] - .05
                    and w["end"] <= s["source_end"] + .05] for s in d["shots"]]
            d = inject_fx(st, d, wbs, log=self.log)
            out = Path(edl).with_name("edl_with_fx.json")
            out.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
            self.edl_path = out
            self.log(f"🎇 {len(d['effects'])} efek terinjeksi → {out}")
        self.worker(job)

    def do_render(self):
        edl = getattr(self, "edl_path", None)
        tl = getattr(self, "timeline_path", None)
        if not (edl and tl):
            return self.log("⚠️ Butuh EDL + timeline (jalankan langkah 2 & 4–5).")
        st = self._settings()

        def job():
            from ai_director.renderer import render
            d = json.loads(Path(edl).read_text(encoding="utf-8"))
            t = json.loads(Path(tl).read_text(encoding="utf-8"))
            final = Path(tl).parent / "FINAL.mp4"
            render(st, d, t, final, Path(tl).parent / "render_tmp", log=self.log)
            self.log(f"🎬 SIAP UNGGAH: {final}")
        self.worker(job)

    def do_all(self):
        url = self._source()
        if not url:
            return
        st = self._settings()
        rec = getattr(self, "rec_path", None)

        def job():
            res = self._full(st, url, rec)
            if res and res.get("output"):
                self.log(f"\n🎉 SIAP UNGGAH: {res['output']}")
        self.worker(job)

    def _full(self, st, url, rec):
        from ai_director import ingest, transcribe
        from ai_director.timeline import build_timeline
        from ai_director.cleanup import cleanup
        from ai_director.fx import inject_fx
        from ai_director.renderer import render
        self.log("[1/6] Ingesti…"); proj = ingest.ingest(url, Path("projects/_probe"), log=self.log)
        pdir = st.project_dir(proj["slug"])
        segs, words = transcribe.segments_from_source(proj["video"], st, log=self.log)
        self.log("[2/6] Penyutradaraan AI…"); tl = build_timeline(st, proj, segs, log=self.log)
        (pdir / "timeline.json").write_text(json.dumps(tl, indent=2, ensure_ascii=False), encoding="utf-8")
        self.timeline_path = pdir / "timeline.json"
        if not rec:
            self.log("[3/6] Membuka mode teleprompter…")
            from ai_director.recorder import Recorder
            Recorder(tl, pdir / "session.json", log=self.log).run()
            rec = self.rec_path
        if not rec:
            self.log("⛔ Belum ada file rekaman — pipeline berhenti setelah Langkah 2."); return {}
        self.log("[4/6] Kurasi cacat…"); edl = cleanup(st, Path(rec), tl, log=self.log)
        w = transcribe(rec, st.whisper_model)[1]
        wbs = [[x for x in w if x["start"] >= s["source_start"] - .05
                and x["end"] <= s["source_end"] + .05] for s in edl["shots"]]
        self.log("[5/6] Injeksi efek…"); edl = inject_fx(st, edl, wbs, log=self.log)
        (pdir / "edl_with_fx.json").write_text(json.dumps(edl, indent=2, ensure_ascii=False), encoding="utf-8")
        self.edl_path = pdir / "edl_with_fx.json"
        self.log("[6/6] Render final…")
        final = pdir / "FINAL.mp4"
        render(st, edl, tl, final, pdir / "render_tmp", log=self.log)
        return {"output": str(final)}

    def open_folder(self):
        import subprocess, sys
        p = getattr(self, "pdir", Path("projects"))
        if sys.platform == "win32":
            subprocess.Popen(["explorer", str(p)])
        else:
            self.log(f"Folder proyek: {p.resolve()}")

    def do_keys(self):
        w = tk.Toplevel(self.root); w.title("Multi-API Key"); w.geometry("460x360")
        w.configure(bg="#101014")
        tk.Label(w, text="Masukkan key provider mana pun (yang pertama aktif dipakai).\n"
                         "Tanpa key: sistem memakai sutradara heuristik lokal.",
                 bg="#101014", fg="#9aa0a6", justify="left").pack(padx=12, pady=8)
        entries = {}
        for pr in PROVIDERS:
            row = ttk.Frame(w); row.pack(fill="x", padx=12, pady=2)
            ttk.Label(row, text=pr.ljust(9)).pack(side="left")
            e = ttk.Entry(row, show="•", width=34); e.pack(side="left", padx=6)
            cur = self.st.api_keys.get(pr, "")
            if cur:
                e.insert(0, cur)
            entries[pr] = e

        def save():
            for pr, e in entries.items():
                v = e.get().strip()
                if v and v != "local":
                    self.st.set_key(pr, v)
            self.log("🔑 API key disimpan (provider aktif: %s)" % self.st.active_provider())
            w.destroy()
        ttk.Button(w, text="Simpan", command=save).pack(pady=10)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
