"""AI Auto-Director Engine — sistem penyutradaraan, perekaman terpandu,
kurasi cacat otomatis, injeksi efek kontekstual, dan render final.

Modul:
  config     -> manajemen API key multi-provider & pengaturan
  llm        -> jembatan LLM (OpenAI / Gemini / Groq / Ollama) + fallback heuristik
  transcribe -> Whisper lokal (faster-whisper) + parsing VTT/SRT + word timestamps
  ingest     -> pengunduhan yt-dlp (video mentah + subtitle)
  timeline   -> langkah 2: JSON Timeline Engine
  recorder   -> langkah 3: Mode Perekaman Terpandu (Smart Teleprompter)
  cleanup    -> langkah 4: AI Smart Cleanup -> EDL
  fx         -> langkah 5: AI FX & Motion Engine (efek ke dalam EDL)
  renderer   -> langkah 6: Compositing, Audio Ducking, Render FFmpeg (NVENC/QSV)
"""

__version__ = "1.0.0"
