---
title: EduVid Studio
emoji: 🎬
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# EduVid Studio

Aplikasi web otomatis (100% gratis) untuk memproduksi video edukasi infografis berbahasa Indonesia menggunakan Gemini 2.5 Flash, Pollinations.ai, Python Pillow, Microsoft Edge TTS, dan FFmpeg.

## Deploy ke Hugging Face Spaces (Docker)
1. Buat Space baru dengan SDK **Docker**.
2. Unggah seluruh isi folder ini ke repository Space Anda.
3. Buka tab **Settings → Variables and secrets**, lalu tambahkan Secrets:
   - `GEMINI_API_KEY`: API key dari Google AI Studio
   - `APP_PIN`: PIN keamanan untuk mengakses studio (mis. `123456`)
4. (Opsional) Tambahkan file musik latar `.mp3` ke folder `assets/bgm/`. Jika kosong, video tetap dirender tanpa BGM.

## Jalankan Lokal
```bash
cp .env.example .env
# Isi GEMINI_API_KEY dan APP_PIN di .env
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 7860 --reload
```
