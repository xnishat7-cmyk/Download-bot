#!/usr/bin/env python3
# ============================================================
# XZ Music & Video Downloader Bot
# Developed by: https://t.me/XVIBERZ
# ============================================================

import os
import sys
import re
import json
import time
import html
import logging
import tempfile
import subprocess
import urllib.parse
import urllib.request
from dotenv import load_dotenv
import requests
from PIL import Image
import telebot
from telebot import types

# Force immediate unbuffered console output
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Load configuration
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_BASE = os.getenv("API_BASE", "https://honeysee-api.vqx.workers.dev").rstrip("/")
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB Telegram Bot upload limit

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("XZMusicBot")

if not BOT_TOKEN:
    logger.error("BOT_TOKEN is missing! Please configure .env.")
    sys.exit(1)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# Reusable HTTP session with keep-alive
http_session = requests.Session()
http_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
})

# In-memory search cache {chat_id: {vid: metadata}}
SEARCH_CACHE = {}


def parse_duration_seconds(d_str: str) -> int:
    """Parse 'MM:SS' or 'HH:MM:SS' into seconds."""
    if not d_str or d_str in ("N/A", "—", "-"):
        return 0
    parts = d_str.strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except Exception:
        return 0
    return 0


def format_views(view_str: str) -> str:
    """Clean up view count string e.g. '24,767,531 views' -> '24.7M views'."""
    if not view_str or view_str in ("—", "N/A"):
        return "—"
    num_match = re.search(r"([\d,]+)", view_str)
    if num_match:
        try:
            num = int(num_match.group(1).replace(",", ""))
            if num >= 1_000_000_000:
                return f"{num / 1_000_000_000:.1f}B views"
            elif num >= 1_000_000:
                return f"{num / 1_000_000:.1f}M views"
            elif num >= 1_000:
                return f"{num / 1_000:.1f}K views"
            else:
                return f"{num} views"
        except Exception:
            return view_str
    return view_str


def search_youtube(query: str, limit: int = 5):
    """Fast, lightweight YouTube search with rich metadata."""
    try:
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            html_doc = response.read().decode("utf-8")

        match = re.search(r"var ytInitialData = ({.*?});</script>", html_doc)
        if not match:
            match = re.search(r"window\[\"ytInitialData\"\] = ({.*?});</script>", html_doc)

        if not match:
            return []

        data = json.loads(match.group(1))
        contents = (
            data.get("contents", {})
            .get("twoColumnSearchResultsRenderer", {})
            .get("primaryContents", {})
            .get("sectionListRenderer", {})
            .get("contents", [])
        )

        results = []
        for section in contents:
            items = section.get("itemSectionRenderer", {}).get("contents", [])
            for item in items:
                v = item.get("videoRenderer")
                if v and "videoId" in v:
                    vid = v.get("videoId")
                    title = v.get("title", {}).get("runs", [{}])[0].get("text", "Unknown Title")
                    duration = v.get("lengthText", {}).get("simpleText", "—")
                    channel = v.get("ownerText", {}).get("runs", [{}])[0].get("text", "Unknown Artist")
                    raw_views = v.get("viewCountText", {}).get("simpleText", "—")
                    views = format_views(raw_views)
                    published = v.get("publishedTimeText", {}).get("simpleText", "—")
                    thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"

                    results.append({
                        "id": vid,
                        "title": title,
                        "duration": duration,
                        "channel": channel,
                        "views": views,
                        "published": published,
                        "thumb": thumb
                    })
                    if len(results) >= limit:
                        return results
        return results
    except Exception as e:
        logger.error(f"Search error for query '{query}': {e}")
        return []


def extract_video_id(text: str):
    """Extract YouTube 11-char video ID from text or link."""
    text = text.strip()
    if re.match(r"^[a-zA-Z0-9_-]{11}$", text):
        return text
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11})",
        r"youtu\.be\/([0-9A-Za-z_-]{11})",
        r"youtube\.com\/shorts\/([0-9A-Za-z_-]{11})"
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(1)
    return None


def prepare_thumbnail(thumb_url: str, output_path: str) -> bool:
    """Download and resize thumbnail to Telegram's requirement (<= 320x320 JPEG)."""
    try:
        req = urllib.request.Request(thumb_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = resp.read()

        tmp_raw = output_path + ".raw"
        with open(tmp_raw, "wb") as f:
            f.write(data)

        with Image.open(tmp_raw) as img:
            img.thumbnail((320, 320))
            img.convert("RGB").save(output_path, "JPEG", quality=85)

        if os.path.exists(tmp_raw):
            os.remove(tmp_raw)

        return os.path.exists(output_path) and os.path.getsize(output_path) > 500
    except Exception as e:
        logger.warning(f"Failed to prepare thumbnail: {e}")
        return False


def mux_video_audio(video_path: str, audio_path: str, output_path: str) -> bool:
    """Losslessly map/mux video and audio streams together into MP4."""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c", "copy",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-movflags", "+faststart",
        output_path
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
        return res.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    except Exception as e:
        logger.warning(f"Muxing failed: {e}")
        return False


def download_with_progress(url: str, output_path: str, chat_id: int, msg_id: int, title: str, channel: str, duration: str, views: str, media_type: str = "Audio") -> bool:
    """Stream download file with live animated progress bar and percentage."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "*/*"
        }
    )

    last_update_time = [0.0]
    start_time = time.time()
    downloaded = 0

    clean_title = html.escape(title)
    clean_channel = html.escape(channel)
    clean_views = html.escape(views)

    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            total_bytes = int(resp.headers.get("Content-Length", 0))
            total_mb = round(total_bytes / (1024 * 1024), 1) if total_bytes else 0

            with open(output_path, "wb") as f_out:
                while True:
                    chunk = resp.read(128 * 1024)
                    if not chunk:
                        break
                    f_out.write(chunk)
                    downloaded += len(chunk)

                    now = time.time()
                    if now - last_update_time[0] >= 2.0:
                        last_update_time[0] = now
                        pct = int((downloaded / total_bytes) * 100) if total_bytes else 0
                        pct = min(pct, 99)
                        filled = int(pct / 10)
                        bar = "▰" * filled + "▱" * (10 - filled)
                        dl_mb = round(downloaded / (1024 * 1024), 1)
                        elapsed = now - start_time
                        speed_kb = round((downloaded / 1024) / elapsed, 1) if elapsed > 0 else 0
                        speed_str = f"{speed_kb:.0f} KB/s" if speed_kb < 1024 else f"{(speed_kb/1024):.1f} MB/s"

                        size_text = f"<code>{dl_mb} MB / {total_mb} MB</code>" if total_mb else f"<code>{dl_mb} MB</code>"
                        views_line = f"  •  👁 <b>Views:</b> <code>{clean_views}</code>" if clean_views and clean_views != "—" else ""
                        status_text = (
                            f"⚡ <b>Downloading {media_type}...</b>\n\n"
                            "<blockquote>"
                            f"🎵 <b>{clean_title}</b>\n"
                            f"📺 <b>Channel:</b> {clean_channel}\n"
                            f"⏰ <b>Duration:</b> <code>{duration}</code>{views_line}"
                            "</blockquote>\n\n"
                            f"<code>[{bar}]</code> <b>{pct}%</b>\n"
                            f"📊 <b>Size:</b> {size_text}  •  ⚡ <code>{speed_str}</code>"
                        )
                        try:
                            bot.edit_message_text(status_text, chat_id=chat_id, message_id=msg_id)
                        except Exception:
                            pass
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as e:
        logger.error(f"Download stream error: {e}")
        return False


def get_bold_start_text(user) -> str:
    """Clean, bold welcome message matching user's exact specification."""
    user_name = html.escape(user.first_name if user and user.first_name else "there")
    user_id = user.id if user else 0
    mention = f'<a href="tg://user?id={user_id}"><b>{user_name}</b></a>' if user_id else f"<b>{user_name}</b>"

    return (
        f"Hi, {mention}! Turn a YouTube link or title into audio or video.\n\n"
        "<blockquote>"
        "🎵 <b>Download MP3 audio</b>\n"
        "🎬 <b>Download MP4 video</b>\n"
        "🔍 <b>Search YouTube</b>"
        "</blockquote>\n\n"
        "💡 <i>Use a title, artist, or direct YouTube link.</i>\n"
        "⚡ <i>Powered by XVIBERZ</i>"
    )


def get_clean_start_keyboard() -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_search = types.InlineKeyboardButton("🔍 Search Music", callback_data="info:search_guide")
    btn_status = types.InlineKeyboardButton("⚡ Server Status", callback_data="info:api_status")
    markup.add(btn_search, btn_status)
    return markup


@bot.message_handler(commands=["start"])
def cmd_start(message):
    text = get_bold_start_text(message.from_user)
    bot.reply_to(message, text, reply_markup=get_clean_start_keyboard())


@bot.message_handler(commands=["help"])
def cmd_help(message):
    help_text = (
        "📖 <b>Bot Commands & Help</b>\n\n"
        "<blockquote>"
        "• <code>/search &lt;query&gt;</code> — Search YouTube\n"
        "• <code>/ping</code> — Check response latency\n"
        "• <code>/status</code> — Check server health"
        "</blockquote>\n\n"
        "💡 <i>Or just type any song or artist name directly in this chat!</i>"
    )
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="info:back_home"))
    bot.reply_to(message, help_text, reply_markup=markup)


@bot.message_handler(commands=["ping"])
def cmd_ping(message):
    start = time.time()
    msg = bot.reply_to(message, "🏓 <i>Pinging...</i>")
    latency = round((time.time() - start) * 1000, 1)
    bot.edit_message_text(f"⚡ <b>Pong!</b> <code>{latency} ms</code>", chat_id=message.chat.id, message_id=msg.message_id)


@bot.message_handler(commands=["status"])
def cmd_status(message):
    wait_msg = bot.reply_to(message, "🔄 <i>Checking status...</i>")
    try:
        r = http_session.get(f"{API_BASE}/health", timeout=5)
        status_text = (
            "🟢 <b>System Status: Operational</b>\n\n"
            "<blockquote>"
            "⚡ <b>Streaming Engine:</b> Active\n"
            "🎵 <b>Audio Quality:</b> 320 kbps\n"
            "🎬 <b>Video Quality:</b> HD MP4\n"
            "🚀 <b>Network:</b> Cloud-Accelerated CDN"
            "</blockquote>"
        ) if r.status_code == 200 else f"🟡 Status Code: {r.status_code}"
    except Exception as e:
        status_text = f"🔴 Connection error: {str(e)}"
    bot.edit_message_text(status_text, chat_id=message.chat.id, message_id=wait_msg.message_id)


@bot.message_handler(commands=["search", "song", "play"])
def cmd_search(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "ℹ️ <b>Usage:</b> <code>/search &lt;song or playlist name&gt;</code>\n<i>Example:</i> <code>/search Kesariya</code>")
        return
    perform_search(message.chat.id, parts[1].strip())


@bot.callback_query_handler(func=lambda call: call.data.startswith("info:"))
def handle_info_callbacks(call):
    action = call.data.split(":")[1]

    if action == "search_guide":
        text = (
            "🔍 <b>How to Search:</b>\n\n"
            "<blockquote>"
            "• Type any song title: <code>Kesariya</code>\n"
            "• Search artist: <code>Tanveer Evan song</code>\n"
            "• Search playlist: <code>Bollywood 90s hits</code>\n"
            "• Direct link: <code>https://youtu.be/...</code>"
            "</blockquote>\n\n"
            "👇 <i>Just type your query in this chat!</i>"
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="info:back_home"))
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif action == "api_status":
        bot.answer_callback_query(call.id, "Checking server status...")
        try:
            r = http_session.get(f"{API_BASE}/health", timeout=5)
            status_desc = "🟢 Online (Optimal)" if r.status_code == 200 else f"🟡 Code {r.status_code}"
        except Exception:
            status_desc = "🔴 Unavailable"

        text = (
            "⚡ <b>Engine Status:</b>\n\n"
            "<blockquote>"
            f"• <b>Status:</b> {status_desc}\n"
            "• <b>Audio:</b> 320 kbps High-Definition\n"
            "• <b>Video:</b> HD MP4 with Audio Mapping\n"
            "• <b>Speed:</b> Cloud CDN Optimized"
            "</blockquote>"
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="info:back_home"))
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

    elif action == "back_home":
        text = get_bold_start_text(call.from_user)
        bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=get_clean_start_keyboard())


@bot.callback_query_handler(func=lambda call: call.data == "close_panel")
def handle_close_panel(call):
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass


@bot.message_handler(func=lambda msg: True and not msg.text.startswith("/"))
def handle_text_query(message):
    query = message.text.strip()
    if not query:
        return

    vid = extract_video_id(query)
    if vid:
        handle_direct_video(message.chat.id, vid)
        return

    perform_search(message.chat.id, query)


def perform_search(chat_id: int, query: str):
    search_msg = bot.send_message(chat_id, f"🔍 <i>Searching for:</i> <b>{html.escape(query)}</b>...")

    results = search_youtube(query, limit=5)
    if not results:
        bot.edit_message_text(
            f"❌ No tracks found for: <b>'{html.escape(query)}'</b>\nPlease check your spelling and try again.",
            chat_id=chat_id,
            message_id=search_msg.message_id
        )
        return

    if chat_id not in SEARCH_CACHE:
        SEARCH_CACHE[chat_id] = {}
    for r in results:
        SEARCH_CACHE[chat_id][r["id"]] = r

    number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    msg_lines = [
        f"🔍 <b>Search Results:</b> <code>{html.escape(query)}</code>\n",
        "<blockquote>"
    ]

    markup = types.InlineKeyboardMarkup()

    for idx, r in enumerate(results):
        emoji_num = number_emojis[idx] if idx < len(number_emojis) else f"{idx+1}."
        clean_title = html.escape(r['title'])
        clean_channel = html.escape(r['channel'])
        duration = r['duration']
        views = r.get('views', '—')

        msg_lines.append(f"{emoji_num} <b>{clean_title}</b>")
        views_info = f" • 👁 <code>{views}</code>" if views and views != "—" else ""
        msg_lines.append(f"└ 👤 <code>{clean_channel}</code> • ⏱ <code>{duration}</code>{views_info}\n")

        btn_a = types.InlineKeyboardButton(f"{emoji_num} 🎵 MP3", callback_data=f"dl:audio:{r['id']}")
        btn_v = types.InlineKeyboardButton(f"{emoji_num} 🎬 MP4", callback_data=f"dl:video:{r['id']}")
        markup.row(btn_a, btn_v)

    msg_lines.append("</blockquote>\n👇 <b>Select quality or format below:</b>")
    markup.row(types.InlineKeyboardButton("🗑 Close", callback_data="close_panel"))

    full_text = "\n".join(msg_lines)
    bot.edit_message_text(full_text, chat_id=chat_id, message_id=search_msg.message_id, reply_markup=markup)


def handle_direct_video(chat_id: int, vid: str):
    status_msg = bot.send_message(chat_id, "🔍 <i>Analyzing video...</i>")
    best_info_url = f"{API_BASE}/best?url={vid}&format=mp4"
    title = "YouTube Video"
    try:
        r = http_session.get(best_info_url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("success"):
                title = data.get("video", {}).get("title", title)
    except Exception:
        pass

    markup = types.InlineKeyboardMarkup(row_width=2)
    btn_audio = types.InlineKeyboardButton("🎵 Download MP3 (320k)", callback_data=f"dl:audio:{vid}")
    btn_video = types.InlineKeyboardButton("🎬 Download MP4 (HD)", callback_data=f"dl:video:{vid}")
    markup.add(btn_audio, btn_video)
    markup.row(types.InlineKeyboardButton("❌ Cancel", callback_data="close_panel"))

    card_text = (
        "🎬 <b>YouTube Media Detected</b>\n\n"
        "<blockquote>"
        f"📌 <b>Title:</b> {html.escape(title)}\n"
        f"🆔 <b>Video ID:</b> <code>{vid}</code>"
        "</blockquote>\n\n"
        "👇 <b>Choose download format:</b>"
    )
    bot.edit_message_text(card_text, chat_id=chat_id, message_id=status_msg.message_id, reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("dl:"))
def handle_download(call):
    parts = call.data.split(":")
    if len(parts) != 3:
        return

    media_type, vid = parts[1], parts[2]
    is_audio = (media_type == "audio")
    req_format = "m4a" if is_audio else "mp4"

    bot.answer_callback_query(call.id, "Starting download...")

    # User info for 'Requested by'
    user = call.from_user
    user_name = html.escape(user.first_name if user and user.first_name else "User")
    user_id = user.id if user else 0
    user_mention = f'<a href="tg://user?id={user_id}">{user_name}</a>' if user_id else user_name

    cached = SEARCH_CACHE.get(call.message.chat.id, {}).get(vid, {})
    title = cached.get("title", "Media Track")
    channel = cached.get("channel", "Artist")
    duration_str = cached.get("duration", "—")
    duration_sec = parse_duration_seconds(duration_str)
    views_str = cached.get("views", "—")
    published_str = cached.get("published", "—")

    clean_title = html.escape(title)
    clean_channel = html.escape(channel)
    clean_views = html.escape(views_str)
    clean_published = html.escape(published_str)

    views_info = f"  •  👁 <b>Views:</b> <code>{clean_views}</code>" if clean_views and clean_views != "—" else ""
    status_msg = bot.send_message(
        call.message.chat.id,
        "⚡ <b>Connecting to server...</b>\n\n"
        "<blockquote>"
        f"🎵 <b>{clean_title}</b>\n"
        f"📺 <b>Channel:</b> {clean_channel}\n"
        f"⏰ <b>Duration:</b> <code>{duration_str}</code>{views_info}"
        "</blockquote>"
    )

    best_api_url = f"{API_BASE}/best?url={vid}&format={req_format}"
    fallback_down_url = f"{API_BASE}/down?url={vid}&format={req_format}"
    selected_download_url = fallback_down_url

    temp_files = []

    try:
        # Step 1: Query API for optimal stream & check audio existence
        video_has_audio = True
        audio_stream_url = None

        try:
            r_info = http_session.get(best_api_url, timeout=10)
            if r_info.status_code == 200:
                meta = r_info.json()
                if meta.get("success"):
                    title = meta.get("video", {}).get("title", title)
                    clean_title = html.escape(title)
                    selected_info = meta.get("selected", {})
                    selected_download_url = selected_info.get("download", fallback_down_url)
                    video_has_audio = selected_info.get("hasAudio", True)
        except Exception as err:
            logger.warning(f"Metadata query warning for {vid}: {err}")

        # If high-res video lacks audio, retrieve separate audio stream URL to map
        needs_audio_mapping = (not is_audio) and (not video_has_audio)
        if needs_audio_mapping:
            try:
                r_aud = http_session.get(f"{API_BASE}/best?url={vid}&format=m4a", timeout=10)
                if r_aud.status_code == 200:
                    d_aud = r_aud.json()
                    if d_aud.get("success"):
                        audio_stream_url = d_aud.get("selected", {}).get("download", f"{API_BASE}/down?url={vid}&format=m4a")
            except Exception:
                pass

        # Step 2: Stream download with live progress bar
        ext = "m4a" if is_audio else "mp4"
        raw_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}")
        temp_files.append(raw_tmp.name)
        raw_tmp.close()

        success = download_with_progress(
            url=selected_download_url,
            output_path=raw_tmp.name,
            chat_id=call.message.chat.id,
            msg_id=status_msg.message_id,
            title=title,
            channel=channel,
            duration=duration_str,
            views=views_str,
            media_type="Audio" if is_audio else "Video"
        )

        if not success or not os.path.exists(raw_tmp.name) or os.path.getsize(raw_tmp.name) == 0:
            raise Exception("Download stream disconnected prematurely.")

        file_size = os.path.getsize(raw_tmp.name)
        file_size_mb = round(file_size / (1024 * 1024), 2)

        # Step 3: Handle Telegram 50 MB limit
        if file_size > MAX_FILE_SIZE_BYTES:
            size_mb = round(file_size / (1024 * 1024), 1)
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🚀 Direct Download Link", url=selected_download_url))
            bot.edit_message_text(
                f"⚠️ <b>File Size: {size_mb} MB</b>\n\n"
                "This file exceeds Telegram's 50 MB bot limit.\n"
                "Download it directly via your browser below:",
                chat_id=call.message.chat.id,
                message_id=status_msg.message_id,
                reply_markup=markup
            )
            return

        final_file_path = raw_tmp.name

        # Step 4: Audio Mapping for video without sound
        if needs_audio_mapping and audio_stream_url:
            bot.edit_message_text(
                "⚡ <b>Mapping Audio Track...</b>\n\n"
                "<blockquote>"
                f"🎬 <b>{clean_title}</b>\n"
                "🔊 Combining high-definition video with audio stream..."
                "</blockquote>",
                chat_id=call.message.chat.id,
                message_id=status_msg.message_id
            )
            aud_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".m4a")
            temp_files.append(aud_tmp.name)
            aud_tmp.close()

            aud_ok = download_with_progress(
                url=audio_stream_url,
                output_path=aud_tmp.name,
                chat_id=call.message.chat.id,
                msg_id=status_msg.message_id,
                title=title,
                channel=channel,
                duration=duration_str,
                views=views_str,
                media_type="Audio Track"
            )

            if aud_ok:
                muxed_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
                temp_files.append(muxed_tmp.name)
                muxed_tmp.close()

                if mux_video_audio(raw_tmp.name, aud_tmp.name, muxed_tmp.name):
                    final_file_path = muxed_tmp.name
                    file_size_mb = round(os.path.getsize(final_file_path) / (1024 * 1024), 2)

        # Step 5: Prepare thumbnail (resized to <= 320x320)
        thumb_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        temp_files.append(thumb_tmp.name)
        thumb_tmp.close()

        has_thumb = prepare_thumbnail(f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", thumb_tmp.name)

        # Step 6: Upload to Telegram with live status
        bot.edit_message_text(
            "⚡ <b>Uploading to Telegram...</b>\n\n"
            "<blockquote>"
            f"🎵 <b>{clean_title}</b>\n"
            f"📺 <b>Channel:</b> {clean_channel}\n"
            f"⏰ <b>Duration:</b> {duration_str}"
            "</blockquote>\n\n"
            "<code>[▰▰▰▰▰▰▰▰▰▰]</code> <b>100%</b>\n"
            "📤 <i>Delivering track to your chat...</i>",
            chat_id=call.message.chat.id,
            message_id=status_msg.message_id
        )

        # Build Rich Song Information Block
        fmt_label = "MP3 audio" if is_audio else "MP4 video"
        quality_label = "320 kbps" if is_audio else "HD 720p / 1080p"

        extra_lines = []
        if clean_views and clean_views != "—":
            extra_lines.append(f"👁 <b>Views:</b> {clean_views}")
        if clean_published and clean_published != "—":
            extra_lines.append(f"📅 <b>Uploaded:</b> {clean_published}")
        extra_lines.append(f"📦 <b>File Size:</b> {file_size_mb} MB")
        extra_lines_str = "\n".join(extra_lines)

        caption_text = (
            f"<b>{clean_title}</b>\n\n"
            "<blockquote>"
            f"📺 <b>Channel:</b> {clean_channel}\n"
            f"⏰ <b>Duration:</b> {duration_str}\n"
            f"{extra_lines_str}\n"
            f"🎧 <b>Format:</b> {fmt_label}\n"
            f"🛠 <b>Quality:</b> {quality_label}\n"
            f"🙋‍♂️ <b>Requested by:</b> {user_mention}\n"
            "🎵 <b>Download by:</b> XVIBERZ"
            "</blockquote>"
        )

        # Interactive buttons: Open on YouTube + Switch format
        dl_markup = types.InlineKeyboardMarkup()
        btn_yt = types.InlineKeyboardButton("🔗 Open on YouTube", url=f"https://www.youtube.com/watch?v={vid}")
        if is_audio:
            btn_alt = types.InlineKeyboardButton("🎬 Download Video (MP4)", callback_data=f"dl:video:{vid}")
            dl_markup.row(btn_yt, btn_alt)
        else:
            btn_alt = types.InlineKeyboardButton("🎵 Download Audio (MP3)", callback_data=f"dl:audio:{vid}")
            dl_markup.row(btn_yt, btn_alt)

        sent = False

        if is_audio:
            # Attempt 1: send_audio with thumbnail
            if has_thumb:
                try:
                    with open(final_file_path, "rb") as a_file, open(thumb_tmp.name, "rb") as t_file:
                        bot.send_audio(
                            call.message.chat.id,
                            a_file,
                            title=title,
                            performer=channel,
                            duration=duration_sec,
                            thumb=t_file,
                            caption=caption_text,
                            reply_markup=dl_markup,
                            timeout=120
                        )
                    sent = True
                except Exception as e1:
                    logger.warning(f"send_audio with thumb failed: {e1}")

            # Attempt 2: send_audio without thumbnail
            if not sent:
                try:
                    with open(final_file_path, "rb") as a_file:
                        bot.send_audio(
                            call.message.chat.id,
                            a_file,
                            title=title,
                            performer=channel,
                            duration=duration_sec,
                            caption=caption_text,
                            reply_markup=dl_markup,
                            timeout=120
                        )
                    sent = True
                except Exception as e2:
                    logger.warning(f"send_audio without thumb failed: {e2}")

            # Attempt 3: send_document
            if not sent:
                clean_filename = re.sub(r'[\\/*?:"<>|]', "", title)[:50]
                with open(final_file_path, "rb") as a_file:
                    bot.send_document(
                        call.message.chat.id,
                        a_file,
                        caption=caption_text,
                        reply_markup=dl_markup,
                        visible_file_name=f"{clean_filename}.mp3",
                        timeout=120
                    )
                sent = True

        else:
            # Video send
            try:
                with open(final_file_path, "rb") as v_file:
                    thumb_f = open(thumb_tmp.name, "rb") if has_thumb else None
                    bot.send_video(
                        call.message.chat.id,
                        v_file,
                        caption=caption_text,
                        duration=duration_sec,
                        thumb=thumb_f,
                        reply_markup=dl_markup,
                        supports_streaming=True,
                        timeout=180
                    )
                    if thumb_f:
                        thumb_f.close()
                sent = True
            except Exception as ev:
                logger.warning(f"send_video failed: {ev}")
                clean_filename = re.sub(r'[\\/*?:"<>|]', "", title)[:50]
                with open(final_file_path, "rb") as v_file:
                    bot.send_document(
                        call.message.chat.id,
                        v_file,
                        caption=caption_text,
                        reply_markup=dl_markup,
                        visible_file_name=f"{clean_filename}.mp4",
                        timeout=180
                    )
                sent = True

        # Clean status message after delivery
        try:
            bot.delete_message(call.message.chat.id, status_msg.message_id)
        except Exception:
            pass

    except Exception as exc:
        logger.error(f"Error handling download for {vid}: {exc}", exc_info=True)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🚀 Direct Download Link", url=selected_download_url))
        bot.edit_message_text(
            "❌ <b>Download Error:</b>\n\n"
            "<blockquote>"
            "Could not complete delivery directly to Telegram.\n"
            "You can download your file via the link below:"
            "</blockquote>",
            chat_id=call.message.chat.id,
            message_id=status_msg.message_id,
            reply_markup=markup
        )

    finally:
        for f_path in temp_files:
            try:
                if os.path.exists(f_path):
                    os.remove(f_path)
            except Exception:
                pass


def main():
    print("=" * 60)
    print("🚀 XZ Music Bot is running!")
    print("🤖 Connected to Telegram successfully...")
    print("=" * 60)
    bot.infinity_polling(timeout=60, long_polling_timeout=25, restart_on_change=False)


if __name__ == "__main__":
    main()
