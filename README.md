# 🎧 XZ Music & Video Downloader Bot

High-speed Telegram music and video downloader bot with crystal clear audio conversion, ID3 metadata tags, album cover art, and Telegram audio player integration.

---

## ✨ Features

- **Instant Song Search:** Send any song or artist name to get instant matching YouTube results.
- **True MP3 Encoding:** Uses `ffmpeg` to encode clean, high-grade 192kbps MP3 audio with embedded ID3 tags.
- **Album Cover Art:** Automatically fetches and formats artwork (scaled to Telegram's 320x320 specification) for native in-player display.
- **Video Downloads:** One-click HD MP4 video downloads with streaming support.
- **Fail-Safe Delivery:** 3-tier delivery mechanism (`send_audio` with thumb ➔ `send_audio` without thumb ➔ `send_document`).
- **Large File Support:** Generates direct high-speed download links for media exceeding Telegram's 50 MB bot limit.
- **Completely English UI:** Modern, clean, and intuitive design without exposing backend API details.

---

## 🚀 Quick Setup

1. Configure your `.env` file:
   ```env
   BOT_TOKEN=your_telegram_bot_token_here
   API_BASE=https://honeysee-api.vqx.workers.dev
   ```

2. Run the bot:
   ```bash
   ./run.sh
   ```
   Or manually:
   ```bash
   ./venv/bin/python3 bot.py
   ```
