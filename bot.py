#!/usr/bin/env python3
"""
Lawctopus Law School Bot — RAILWAY EDITION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✅ Railway-compatible (ephemeral /tmp paths, env-var config)
  ✅ Bunny CDN fast-upload for large videos (>100 MB) — bypasses Telegram rate limits
  ✅ Telethon upload at MAX speed — 5 MB parts, 20 parallel connections (fallback)
  ✅ Live upload progress bar (current/total, speed, ETA) via Telethon callback
  ✅ Full Telegram flood-wait / rate-limit handling with exponential backoff
  ✅ yt-dlp live download progress
  ✅ Animated car image on /start
  ✅ /extract button per course
  ✅ Pause / resume / stop
"""

import asyncio
import io
import logging
import os
import re
import shutil
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiofiles
import httpx
import requests
import yt_dlp
from PIL import Image

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)

from telethon import TelegramClient, errors as tl_errors
from telethon.tl.types import DocumentAttributeVideo

try:
    from html_gen import generate_course_html, generate_combined_html
except ImportError:
    def generate_course_html(*a, **kw): return "<html><body>html_gen.py not found</body></html>"
    def generate_combined_html(*a, **kw): return "<html><body>html_gen.py not found</body></html>"

# ═══════════════════════════════════════════════════════════════════
#  CONFIG  — all values come from environment variables on Railway
# ═══════════════════════════════════════════════════════════════════

def _require_env(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        print(f"ERROR: required environment variable '{key}' is not set.", file=sys.stderr)
        sys.exit(1)
    return val

BOT_TOKEN = _require_env("BOT_TOKEN")
API_ID    = int(_require_env("API_ID"))
API_HASH  = _require_env("API_HASH")
BASE_URL  = os.environ.get("BASE_URL", "https://www.lawctopuslawschool.com")

# ── Bunny CDN (optional) ─────────────────────────────────────────
# Set these three env vars to enable fast CDN uploads for large videos.
# Leave unset to fall back to direct Telegram upload for all files.
BUNNY_STORAGE_ZONE = os.environ.get("BUNNY_STORAGE_ZONE", "").strip()
BUNNY_API_KEY      = os.environ.get("BUNNY_API_KEY", "").strip()
BUNNY_HOSTNAME     = os.environ.get("BUNNY_HOSTNAME", "").strip()
# Public CDN pull-zone base URL, e.g. "https://myzone.b-cdn.net"
BUNNY_CDN_BASE_URL = os.environ.get("BUNNY_CDN_BASE_URL", "").strip()
# Files larger than this threshold are routed through Bunny CDN
BUNNY_THRESHOLD_BYTES = 100 * 1024 * 1024  # 100 MB

# Railway has an ephemeral filesystem; /tmp is the safe writable location.
_TMP      = Path(tempfile.gettempdir())
DL_DIR    = _TMP / "lls_downloads"
HTML_DIR  = _TMP / "lls_html"
SESSION   = str(_TMP / "lls_telethon")
DL_DIR.mkdir(parents=True, exist_ok=True)
HTML_DIR.mkdir(parents=True, exist_ok=True)

INTER_MSG_DELAY   = 0.5
GROUP_MSG_DELAY   = 1.5
MAX_FLOOD_RETRIES = 8

UPLOAD_PART_KB  = 5120   # 5 MB parts — reduces per-chunk overhead for large files
UPLOAD_WORKERS  = 20     # 20 parallel connections — maximises throughput to Telegram

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    stream=sys.stdout,          # Railway surfaces stdout logs
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
#  ANIMATED CAR SVG
# ═══════════════════════════════════════════════════════════════════

CAR_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 420" width="900" height="420">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#050510"/><stop offset="100%" stop-color="#0d001f"/>
    </linearGradient>
    <linearGradient id="carbody" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#ff2d55"/><stop offset="55%" stop-color="#c0002a"/><stop offset="100%" stop-color="#700015"/>
    </linearGradient>
    <linearGradient id="win" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#a0e8ff" stop-opacity="0.92"/><stop offset="100%" stop-color="#0070c0" stop-opacity="0.75"/>
    </linearGradient>
    <radialGradient id="whl" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#4a4a4a"/><stop offset="65%" stop-color="#1e1e1e"/><stop offset="100%" stop-color="#0a0a0a"/>
    </radialGradient>
    <filter id="glow" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="5" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="rearglow" x="-60%" y="-60%" width="220%" height="220%">
      <feGaussianBlur stdDeviation="8" result="blur"/>
      <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <rect width="900" height="420" fill="url(#bg)"/>
  <g fill="#ffffff" opacity="0.5">
    <circle cx="50" cy="30" r="1"/><circle cx="150" cy="60" r="1.2"/>
    <circle cx="300" cy="20" r="1"/><circle cx="500" cy="45" r="0.8"/>
    <circle cx="700" cy="25" r="1.2"/><circle cx="820" cy="55" r="1"/>
    <circle cx="80" cy="90" r="0.8"/><circle cx="430" cy="80" r="1"/>
    <circle cx="600" cy="70" r="0.9"/><circle cx="760" cy="90" r="1.1"/>
  </g>
  <rect x="0" y="330" width="900" height="90" fill="#0c0c18"/>
  <rect x="0" y="328" width="900" height="5" fill="#1e1e38" rx="2"/>
  <g fill="#ffffff14">
    <rect y="352" height="9" width="130" rx="4"><animate attributeName="x" from="0" to="-170" dur="0.7s" repeatCount="indefinite"/></rect>
    <rect y="352" height="9" width="130" rx="4"><animate attributeName="x" from="170" to="0" dur="0.7s" repeatCount="indefinite"/></rect>
    <rect y="352" height="9" width="130" rx="4"><animate attributeName="x" from="340" to="170" dur="0.7s" repeatCount="indefinite"/></rect>
    <rect y="352" height="9" width="130" rx="4"><animate attributeName="x" from="510" to="340" dur="0.7s" repeatCount="indefinite"/></rect>
    <rect y="352" height="9" width="130" rx="4"><animate attributeName="x" from="680" to="510" dur="0.7s" repeatCount="indefinite"/></rect>
    <rect y="352" height="9" width="130" rx="4"><animate attributeName="x" from="850" to="680" dur="0.7s" repeatCount="indefinite"/></rect>
  </g>
  <g>
    <animateTransform attributeName="transform" type="translate" values="0,0;0,-7;0,-3;0,-8;0,0" dur="2s" repeatCount="indefinite"/>
    <rect x="130" y="295" width="640" height="35" rx="6" fill="#5a0010"/>
    <path d="M150,295 L220,210 Q252,185 300,180 L590,180 Q640,180 675,210 L760,295 Z" fill="url(#carbody)"/>
    <path d="M270,290 L305,225 Q322,205 348,202 L552,202 Q578,205 595,225 L630,290 Z" fill="url(#win)" opacity="0.88"/>
    <line x1="450" y1="202" x2="450" y2="290" stroke="#ffffff30" stroke-width="2"/>
    <ellipse cx="748" cy="268" rx="26" ry="13" fill="#fff8c0" filter="url(#glow)" opacity="0.98"/>
    <ellipse cx="748" cy="268" rx="16" ry="8" fill="#ffffff" filter="url(#glow)"/>
    <ellipse cx="152" cy="265" rx="20" ry="11" fill="#ff0030" filter="url(#rearglow)" opacity="0.95"/>
    <ellipse cx="152" cy="265" rx="11" ry="6" fill="#ff6688" filter="url(#rearglow)"/>
    <ellipse cx="155" cy="305" rx="20" ry="8" fill="#ff7700" opacity="0.85">
      <animate attributeName="rx" values="20;13;24;16;20" dur="0.18s" repeatCount="indefinite"/>
      <animate attributeName="ry" values="8;13;5;10;8" dur="0.18s" repeatCount="indefinite"/>
    </ellipse>
    <ellipse cx="134" cy="305" rx="11" ry="5" fill="#ffcc00" opacity="0.9">
      <animate attributeName="rx" values="11;6;15;8;11" dur="0.13s" repeatCount="indefinite"/>
    </ellipse>
    <circle cx="630" cy="330" r="52" fill="url(#whl)"/>
    <circle cx="630" cy="330" r="38" fill="#181818"/>
    <g><animateTransform attributeName="transform" type="rotate" from="0 630 330" to="360 630 330" dur="0.45s" repeatCount="indefinite"/>
      <line x1="630" y1="292" x2="630" y2="368" stroke="#666" stroke-width="5" stroke-linecap="round"/>
      <line x1="592" y1="330" x2="668" y2="330" stroke="#666" stroke-width="5" stroke-linecap="round"/>
    </g>
    <circle cx="630" cy="330" r="7" fill="#cccccc"/>
    <circle cx="260" cy="330" r="52" fill="url(#whl)"/>
    <circle cx="260" cy="330" r="38" fill="#181818"/>
    <g><animateTransform attributeName="transform" type="rotate" from="0 260 330" to="360 260 330" dur="0.45s" repeatCount="indefinite"/>
      <line x1="260" y1="292" x2="260" y2="368" stroke="#666" stroke-width="5" stroke-linecap="round"/>
      <line x1="222" y1="330" x2="298" y2="330" stroke="#666" stroke-width="5" stroke-linecap="round"/>
    </g>
    <circle cx="260" cy="330" r="7" fill="#cccccc"/>
  </g>
  <text x="450" y="62" text-anchor="middle" font-family="'Arial Black', Impact, sans-serif" font-size="40" font-weight="900" letter-spacing="6" fill="#ff2d55" filter="url(#glow)">LAWCTOPUS BOT</text>
  <text x="450" y="90" text-anchor="middle" font-family="Arial, sans-serif" font-size="15" letter-spacing="8" fill="#ffffff55">COURSE DOWNLOADER</text>
</svg>"""


def svg_to_jpg_bytes(svg: str) -> Optional[bytes]:
    try:
        import cairosvg
        png = cairosvg.svg2png(bytestring=svg.encode(), output_width=900)
        img = Image.open(io.BytesIO(png)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=92)
        return buf.getvalue()
    except Exception as e:
        logger.warning("SVG→JPG: %s", e)
        return None

# ═══════════════════════════════════════════════════════════════════
#  TELETHON CLIENT
# ═══════════════════════════════════════════════════════════════════

_tc: Optional[TelegramClient] = None


async def get_tc() -> TelegramClient:
    global _tc
    if _tc and _tc.is_connected():
        return _tc
    _tc = TelegramClient(
        SESSION, API_ID, API_HASH,
        connection_retries=None,
        sequential_updates=False,
    )
    await _tc.start(bot_token=BOT_TOKEN)
    logger.info("Telethon connected")
    return _tc


# ═══════════════════════════════════════════════════════════════════
#  RATE-LIMIT AWARE SENDER
# ═══════════════════════════════════════════════════════════════════

_last_send: Dict[int, float] = {}


async def _throttle(chat_id: int, is_group: bool = False):
    delay = GROUP_MSG_DELAY if is_group else INTER_MSG_DELAY
    gap = time.time() - _last_send.get(chat_id, 0)
    if gap < delay:
        await asyncio.sleep(delay - gap)
    _last_send[chat_id] = time.time()


async def safe_send_file(
    tc: TelegramClient,
    chat_id,
    filepath: Path,
    caption: str = "",
    reply_to: int = None,
    progress_cb=None,
    is_group: bool = False,
) -> bool:
    """
    Send *filepath* to *chat_id*.

    For large video files (> BUNNY_THRESHOLD_BYTES) when Bunny CDN is
    configured, the file is first uploaded to Bunny CDN and the resulting
    public URL is sent as a text message.  This bypasses Telegram's strict
    upload rate limits (~300 KB/s) entirely.

    For small files, or when Bunny CDN is not configured, the file is
    uploaded directly to Telegram via Telethon as before.
    """
    is_vid      = filepath.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi"}
    file_size   = filepath.stat().st_size
    use_bunny   = (
        is_vid
        and _bunny_configured()
        and file_size >= BUNNY_THRESHOLD_BYTES
    )

    # ── Fast path: upload to Bunny CDN, then share the URL ──────────
    if use_bunny:
        logger.info(
            "Large video (%s) — routing through Bunny CDN: %s",
            fmt_size(file_size), filepath.name,
        )

        # Build a progress callback that matches the (pct, done, total, speed, eta)
        # signature used by upload_to_bunny_cdn() and feeds into the existing
        # make_upload_progress_cb display logic.
        bunny_prog_cb = None
        if progress_cb:
            # progress_cb here is the Telethon-style (current, total) coroutine
            # produced by make_upload_progress_cb.  We wrap it so Bunny's
            # (pct, done, total, speed, eta) signature maps onto it.
            async def bunny_prog_cb(pct, done, total, speed, eta):  # noqa: E306
                await progress_cb(done, total)

        cdn_url = await upload_to_bunny_cdn(
            filepath,
            progress_cb=bunny_prog_cb,
        )

        if cdn_url:
            # Send the CDN link as a Telegram message — instant delivery.
            size_str = fmt_size(file_size)
            text = (
                f"🎬 *{filepath.stem}*\n\n"
                f"📦 Size: `{size_str}`\n"
                f"🔗 [Download / Watch]({cdn_url})\n\n"
                + (f"_{caption}_" if caption else "")
            ).strip()
            msg = await safe_send_message(
                tc, chat_id, text, reply_to=reply_to, is_group=is_group
            )
            if msg:
                logger.info("Bunny CDN link sent to %s: %s", chat_id, cdn_url)
                return True
            logger.warning("Bunny CDN link message failed — falling back to direct upload")
        else:
            logger.warning("Bunny CDN upload failed — falling back to direct Telegram upload")

    # ── Slow path: direct Telegram upload via Telethon ──────────────
    kwargs = {
        "caption": caption,
        "force_document": not is_vid,
        "part_size_kb": UPLOAD_PART_KB,
        "num_workers": UPLOAD_WORKERS,
    }
    if is_vid:
        kwargs["attributes"] = [DocumentAttributeVideo(0, 0, 0)]
        kwargs["supports_streaming"] = True
        del kwargs["force_document"]
    if reply_to:
        kwargs["reply_to"] = reply_to
    if progress_cb:
        kwargs["progress_callback"] = progress_cb

    for attempt in range(MAX_FLOOD_RETRIES):
        try:
            await _throttle(chat_id if isinstance(chat_id, int) else 0, is_group)
            await tc.send_file(chat_id, str(filepath), **kwargs)
            return True
        except tl_errors.FloodWaitError as e:
            wait = e.seconds + 2
            logger.warning("FloodWait %ds on attempt %d", wait, attempt + 1)
            await asyncio.sleep(wait)
        except tl_errors.SlowModeWaitError as e:
            await asyncio.sleep(e.seconds + 1)
        except tl_errors.ChatWriteForbiddenError:
            logger.error("Bot lacks send permission in chat %s", chat_id)
            return False
        except (ConnectionError, OSError) as e:
            await asyncio.sleep(2 ** attempt)
        except Exception as e:
            logger.error("send_file failed: %s", e)
            return False

    return False


async def safe_send_message(
    tc: TelegramClient,
    chat_id,
    text: str,
    reply_to: int = None,
    is_group: bool = False,
) -> Optional[object]:
    for attempt in range(MAX_FLOOD_RETRIES):
        try:
            await _throttle(chat_id if isinstance(chat_id, int) else 0, is_group)
            return await tc.send_message(chat_id, text, reply_to=reply_to)
        except tl_errors.FloodWaitError as e:
            await asyncio.sleep(e.seconds + 2)
        except tl_errors.SlowModeWaitError as e:
            await asyncio.sleep(e.seconds + 1)
        except Exception:
            await asyncio.sleep(2 ** attempt)
    return None


# ═══════════════════════════════════════════════════════════════════
#  UPLOAD PROGRESS
# ═══════════════════════════════════════════════════════════════════

def make_upload_progress_cb(ptb_msg, filename: str, file_size: int):
    last_edit = [0.0]
    start_t   = [time.time()]

    async def _cb(current: int, total: int):
        now = time.time()
        if now - last_edit[0] < 2.0:
            return
        last_edit[0] = now
        elapsed = max(now - start_t[0], 0.1)
        speed   = current / elapsed
        pct     = (current / total * 100) if total else 0
        eta_sec = int((total - current) / speed) if speed > 0 and total > current else 0
        bar     = _prog_bar(pct)
        txt = (
            f"⬆️ *Uploading…*\n"
            f"`{trunc(filename, 42)}`\n\n"
            f"`[{bar}]` *{pct:.1f}%*\n"
            f"📦 `{fmt_size(current)}` / `{fmt_size(total)}`\n"
            f"⚡ `{fmt_size(int(speed))}/s`\n"
            f"⏱ ETA `{_eta(eta_sec)}`"
        )
        try:
            await ptb_msg.edit_text(txt, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

    return _cb


# ═══════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════

def _prog_bar(pct: float, w: int = 20) -> str:
    f = int(pct / 100 * w)
    return "█" * f + "░" * (w - f)

def _eta(secs: int) -> str:
    if secs <= 0: return "..."
    if secs < 60: return f"{secs}s"
    return f"{secs//60}m{secs%60:02d}s"

def fmt_size(b: int) -> str:
    if b < 1024:    return f"{b} B"
    if b < 1048576: return f"{b/1024:.1f} KB"
    return f"{b/1048576:.1f} MB"

def trunc(text: str, n: int = 48) -> str:
    text = (text or "").strip()
    return text[:n] + "…" if len(text) > n else text

def api_get(path: str, params: dict = None) -> Optional[dict]:
    try:
        r = requests.get(f"{BASE_URL}/wp-json{path}", headers=HEADERS, params=params, timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.error("API %s: %s", path, e)
    return None

# ═══════════════════════════════════════════════════════════════════
#  MEDIA EXTRACTION
# ═══════════════════════════════════════════════════════════════════

def _extract_media(content: str, topic_title: str = "") -> List[Dict]:
    media = []
    for m in re.finditer(
            r'<iframe[^>]*src=["\'](https://(?:iframe|player)\.mediadelivery\.net/embed/[^"\']+)',
            content, re.I):
        url = m.group(1).split("?")[0]
        media.append({"type": "video_link", "url": url, "name": f"Video: {topic_title}", "embed": url})
    for m in re.finditer(
            r'<iframe[^>]*src=["\'](https://player\.vimeo\.com/video/[^"\']+)', content, re.I):
        media.append({"type": "vimeo", "url": m.group(1), "name": f"Vimeo: {topic_title}", "embed": m.group(1)})
    for m in re.finditer(
            r'<iframe[^>]*src=["\'](https://(?:www\.)?youtube\.com/embed/[^"\']+)', content, re.I):
        media.append({"type": "youtube", "url": m.group(1), "name": f"YouTube: {topic_title}", "embed": m.group(1)})
    for m in re.finditer(r'<a[^>]*href=["\']([^"\']+\.pdf[^"\']*)["\'][^>]*>', content, re.I):
        url = m.group(1)
        if url.startswith("//"): url = "https:" + url
        elif url.startswith("/"): url = BASE_URL + url
        fname = url.split("/")[-1].split("?")[0]
        media.append({"type": "pdf", "url": url, "name": fname or f"{topic_title}.pdf"})
    for pat in [
        r'<div[^>]*class=["\'][^"\']*learndash-lesson-materials[^"\']*["\'][^>]*>(.*?)</div>\s*</div>',
        r'<div[^>]*class=["\'][^"\']*lesson-materials[^"\']*["\'][^>]*>(.*?)</div>',
    ]:
        ms = re.search(pat, content, re.I | re.DOTALL)
        if ms:
            for m in re.finditer(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', ms.group(1), re.I):
                url = m.group(1); name = re.sub(r'<[^>]+>', '', m.group(2)).strip()
                if url.startswith("//"): url = "https:" + url
                elif url.startswith("/"): url = BASE_URL + url
                ext = url.split("?")[0].rsplit(".", 1)[-1].lower() if "." in url else ""
                ft  = "pdf" if ext == "pdf" else ("mp4" if ext in ("mp4","webm","mkv") else "pdf")
                media.append({"type": ft, "url": url, "name": name or url.split("/")[-1].split("?")[0]})
    for m in re.finditer(r'<a[^>]*href=["\']([^"\']+\.mp4[^"\']*)["\'][^>]*>', content, re.I):
        url = m.group(1)
        if url.startswith("//"): url = "https:" + url
        elif url.startswith("/"): url = BASE_URL + url
        media.append({"type": "mp4", "url": url, "name": url.split("/")[-1].split("?")[0]})
    for m in re.finditer(r'"(https://[^"]+\.b-cdn\.net/[^"]+\.mp4)"', content):
        url = m.group(1)
        if not any(x["url"] == url for x in media):
            media.append({"type": "mp4", "url": url, "name": url.split("/")[-1].split("?")[0]})
    for m in re.finditer(r'(https://[^"\']+/wp-content/uploads/[^"\']+\.pdf)', content, re.I):
        url = m.group(1)
        if not any(x["url"] == url for x in media):
            media.append({"type": "pdf", "url": url, "name": url.split("/")[-1].split("?")[0]})
    seen = set(); unique = []
    for m in media:
        if m["url"] not in seen:
            seen.add(m["url"]); unique.append(m)
    return unique


async def scrape_topic_page(permalink: str) -> str:
    if not permalink: return ""
    try:
        async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True,
                                     timeout=httpx.Timeout(connect=15, read=30)) as client:
            r = await client.get(permalink); r.raise_for_status(); html = r.text
        for pattern in [
            r'<div[^>]*class=["\'][^"\']*learndash-lesson-content[^"\']*["\'][^>]*>(.*?)</div>\s*</div>',
            r'<div[^>]*class=["\'][^"\']*entry-content[^"\']*["\'][^>]*>(.*?)</div>',
            r'<article[^>]*>(.*?)</article>',
            r'<main[^>]*>(.*?)</main>',
            r'<body[^>]*>(.*?)</body>',
        ]:
            m = re.search(pattern, html, re.I | re.DOTALL)
            if m and len(m.group(1).strip()) > 100:
                return m.group(1)
        return html
    except Exception as e:
        logger.error("Scrape %s: %s", permalink, e); return ""


async def extract_media_for_topic(topic: Dict) -> List[Dict]:
    content   = topic.get("content", "")
    title     = topic.get("title", "Topic")
    permalink = topic.get("permalink", "")
    if not content or len(content.strip()) < 50:
        if permalink:
            scraped = await scrape_topic_page(permalink)
            if scraped:
                content = scraped; topic["content"] = content
    return _extract_media(content, title)

# ═══════════════════════════════════════════════════════════════════
#  VIDEO DOWNLOADER (yt-dlp)
# ═══════════════════════════════════════════════════════════════════

user_state:   Dict[int, dict] = {}
search_mode:  Dict[int, bool] = {}
cancel_flags: Dict[int, bool] = {}
pause_flags:  Dict[int, bool] = {}


async def download_video_ytdlp(
    url: str, dest_dir: Path, status_msg, uid: int = 0
) -> Optional[Path]:
    loop = asyncio.get_event_loop()
    last_edit = [0.0]

    async def _async_prog(d):
        if d["status"] != "downloading": return
        dl    = d.get("downloaded_bytes", 0)
        total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
        spd   = d.get("speed") or 0
        pct   = (dl / total * 100) if total else 0
        now   = asyncio.get_event_loop().time()
        if now - last_edit[0] < 2.0: return
        last_edit[0] = now
        bar = _prog_bar(pct)
        txt = (
            f"⬇️ *Downloading video…*\n"
            f"`[{bar}]` *{pct:.1f}%*\n"
            f"📦 `{fmt_size(dl)}` / `{fmt_size(total)}`\n"
            f"⚡ `{fmt_size(int(spd))}/s`"
        )
        try: await status_msg.edit_text(txt, parse_mode=ParseMode.MARKDOWN)
        except Exception: pass

    def _sync_hook(d):
        if uid and cancel_flags.get(uid): raise Exception("Stopped")
        asyncio.run_coroutine_threadsafe(_async_prog(d), loop)

    opts = {
        "outtmpl": str(dest_dir / "%(title).70s.%(ext)s"),
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "progress_hooks": [_sync_hook],
        "concurrent_fragment_downloads": 10,
        "http_headers": {
            "User-Agent": HEADERS["User-Agent"],
            "Referer": url,
            "Origin": "https://iframe.mediadelivery.net",
        },
        "socket_timeout": 30, "retries": 5, "fragment_retries": 10,
    }

    def _run():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)

    try:
        fname = await asyncio.wait_for(loop.run_in_executor(None, _run), timeout=3600)
        p = Path(fname)
        if not p.exists(): p = p.with_suffix(".mp4")
        if not p.exists():
            files = sorted(dest_dir.glob("*.mp4"), key=os.path.getmtime, reverse=True)
            p = files[0] if files else None
        return p if p and p.exists() else None
    except Exception as e:
        logger.error("yt-dlp: %s", e)
        return None


async def download_file_fast(
    url: str, dest: Path, progress_cb=None, uid: int = 0
) -> Tuple[Optional[Path], Optional[str]]:
    CHUNK = 10 * 1024 * 1024  # 10 MB read chunks — amortises async overhead on fast links
    try:
        async with httpx.AsyncClient(
            headers=HEADERS, follow_redirects=True,
            timeout=httpx.Timeout(connect=15, read=300, write=60, pool=15),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=30,
            ),
            http2=False,  # HTTP/1.1 keep-alive is more reliable for large binary streams
        ) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", 0))
                done = 0; t0 = time.time(); last_cb = 0.0
                async with aiofiles.open(dest, "wb") as fh:
                    async for chunk in resp.aiter_bytes(CHUNK):
                        if uid and cancel_flags.get(uid):
                            raise asyncio.CancelledError("Stopped")
                        while uid and pause_flags.get(uid):
                            await asyncio.sleep(0.5)
                            if cancel_flags.get(uid):
                                raise asyncio.CancelledError("Stopped")
                        await fh.write(chunk)
                        done += len(chunk)
                        now = time.time()
                        if progress_cb and (now - last_cb) > 1.5:
                            last_cb = now
                            speed = done / max(now - t0, 0.1)
                            pct   = (done / total * 100) if total else 0
                            eta   = int((total - done) / speed) if speed > 0 and total > done else 0
                            await progress_cb(pct, done, total, speed, eta)
        return dest, None
    except asyncio.CancelledError:
        dest.unlink(missing_ok=True); return None, "Stopped"
    except Exception as e:
        return None, str(e)

# ═══════════════════════════════════════════════════════════════════
#  BUNNY CDN UPLOAD
# ═══════════════════════════════════════════════════════════════════

def _bunny_configured() -> bool:
    """Return True only when all required Bunny CDN env vars are present."""
    return bool(BUNNY_STORAGE_ZONE and BUNNY_API_KEY and BUNNY_HOSTNAME and BUNNY_CDN_BASE_URL)


async def upload_to_bunny_cdn(
    filepath: Path,
    progress_cb=None,
    remote_name: str = None,
    max_retries: int = 3,
) -> Optional[str]:
    """
    Upload *filepath* to Bunny CDN Storage and return the public CDN URL.

    The file is streamed in 10 MB chunks via a single PUT request so that
    the progress callback receives regular updates.  On transient errors the
    upload is retried up to *max_retries* times with exponential back-off.

    Returns the public CDN URL string on success, or None on failure.
    """
    if not _bunny_configured():
        logger.debug("Bunny CDN not configured — skipping CDN upload")
        return None

    remote_name = remote_name or filepath.name
    # Sanitise the remote filename so it is safe in a URL path
    safe_name   = re.sub(r"[^\w.\-()]", "_", remote_name)
    upload_url  = (
        f"https://{BUNNY_HOSTNAME}/{BUNNY_STORAGE_ZONE}/{safe_name}"
    )
    public_url  = f"{BUNNY_CDN_BASE_URL.rstrip('/')}/{safe_name}"

    file_size = filepath.stat().st_size
    CHUNK     = 10 * 1024 * 1024  # 10 MB chunks

    for attempt in range(1, max_retries + 1):
        try:
            uploaded = 0
            t0       = time.time()

            async def _body_gen():
                nonlocal uploaded
                async with aiofiles.open(filepath, "rb") as fh:
                    while True:
                        chunk = await fh.read(CHUNK)
                        if not chunk:
                            break
                        uploaded += len(chunk)
                        if progress_cb:
                            elapsed = max(time.time() - t0, 0.1)
                            speed   = uploaded / elapsed
                            pct     = (uploaded / file_size * 100) if file_size else 0
                            eta     = int((file_size - uploaded) / speed) if speed > 0 and file_size > uploaded else 0
                            await progress_cb(pct, uploaded, file_size, speed, eta)
                        yield chunk

            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=30, read=600, write=600, pool=30),
            ) as client:
                resp = await client.put(
                    upload_url,
                    content=_body_gen(),
                    headers={
                        "AccessKey":     BUNNY_API_KEY,
                        "Content-Type":  "application/octet-stream",
                        "Content-Length": str(file_size),
                    },
                )

            if resp.status_code in (200, 201):
                logger.info(
                    "Bunny CDN upload OK: %s (%s) → %s",
                    filepath.name, fmt_size(file_size), public_url,
                )
                return public_url

            logger.warning(
                "Bunny CDN attempt %d/%d: HTTP %d — %s",
                attempt, max_retries, resp.status_code, resp.text[:200],
            )

        except Exception as exc:
            logger.warning(
                "Bunny CDN attempt %d/%d error: %s",
                attempt, max_retries, exc,
            )

        if attempt < max_retries:
            await asyncio.sleep(2 ** attempt)

    logger.error("Bunny CDN upload failed after %d attempts: %s", max_retries, filepath.name)
    return None


# ═══════════════════════════════════════════════════════════════════
#  COURSE DATA
# ═══════════════════════════════════════════════════════════════════

def fetch_all_courses() -> List[Dict]:
    data = api_get("/custom/v1/get-courses")
    if not data or data.get("status") != 1: return []
    out = []
    for c in data.get("data", []):
        title = (c.get("course_title") or "").strip()
        cid   = str(c.get("_ID", ""))
        if title and cid:
            out.append({"id": cid, "title": title,
                        "duration": c.get("course_duration", ""),
                        "fee": c.get("course_fee", ""),
                        "link": c.get("learndash_course_link", "")})
    return out


def fetch_course_detail(course_id: str) -> Tuple[Dict, Dict, List[Dict]]:
    data = api_get("/custom/v1/get-single-courses", {"course_id": course_id})
    if not data or data.get("status") != 1: return {}, {}, []
    d = data.get("data", {})
    info = {
        "title":       d.get("course_title", ""),
        "description": (d.get("course_description") or "").strip(),
        "duration":    d.get("course_duration", ""),
        "fee":         d.get("course_fee", ""),
        "link":        d.get("learndash_course_link", ""),
    }
    modules = []
    for lesson in d.get("lessons", []):
        l_title = (lesson.get("post_title") or "").strip()
        if not l_title: continue
        topics = []
        for t in lesson.get("topics", []):
            t_title   = (t.get("post_title") or "").strip()
            if not t_title: continue
            content   = t.get("post_content", "") or ""
            permalink = t.get("permalink", "")
            if not content or len(content.strip()) < 50:
                if permalink: content = f"<!--SCRAPE:{permalink}-->"
            topics.append({"id": str(t.get("ID", "")), "title": t_title,
                           "content": content, "permalink": permalink})
        modules.append({"id": str(lesson.get("ID", "")), "title": l_title, "topics": topics})
    return {}, info, modules

# ═══════════════════════════════════════════════════════════════════
#  KEYBOARDS
# ═══════════════════════════════════════════════════════════════════

def home_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Browse All Courses 📚", callback_data="browse"),
        InlineKeyboardButton("Search 🔍", callback_data="search_prompt"),
    ]])

def courses_kb(courses: List[Dict], page: int = 0, page_size: int = 12):
    start = page * page_size
    chunk = courses[start:start + page_size]
    rows  = [[InlineKeyboardButton(f"  {trunc(c['title'])}", callback_data=f"c_{start+i}")] for i, c in enumerate(chunk)]
    nav   = []
    if page > 0:                         nav.append(InlineKeyboardButton("← Prev", callback_data=f"pg_{page-1}"))
    if start + page_size < len(courses): nav.append(InlineKeyboardButton("Next →", callback_data=f"pg_{page+1}"))
    if nav: rows.append(nav)
    rows.append([InlineKeyboardButton("Search 🔍", callback_data="search_prompt"),
                 InlineKeyboardButton("Refresh 🔄", callback_data="browse")])
    return InlineKeyboardMarkup(rows)

# ═══════════════════════════════════════════════════════════════════
#  EXTRACT — full course download + upload
# ═══════════════════════════════════════════════════════════════════

async def run_extract(
    q,
    context,
    uid: int,
    course: Dict,
    modules: List[Dict],
    target_chat_id: int = None,
):
    cancel_flags[uid] = False
    pause_flags[uid]  = False

    dest_chat = target_chat_id if target_chat_id else uid
    is_group  = target_chat_id is not None

    all_items: List[Tuple[Dict, Dict, Dict]] = []
    for mod in modules:
        for t in mod.get("topics", []):
            for m in await extract_media_for_topic(t):
                all_items.append((mod, t, m))

    if not all_items:
        await q.edit_message_text(
            "❌ No downloadable content found in this course.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="browse")]]),
        )
        return

    prog = await q.edit_message_text(
        f"🚀 *Extract started*\n"
        f"📚 `{trunc(course['title'], 55)}`\n"
        f"📦 *{len(all_items)}* items found\n\n"
        f"{'📤 Sending to group' if is_group else '📥 Sending to you'}\n"
        f"/stop to cancel  •  /pause to pause",
        parse_mode=ParseMode.MARKDOWN,
    )

    tc = await get_tc()

    job_dir = DL_DIR / f"ex_{uid}_{int(time.time())}"
    job_dir.mkdir(exist_ok=True)

    vid_ok = vid_fail = pdf_ok = pdf_fail = 0
    last_prog_edit = [0.0]
    item_num       = [0]
    current_mod_id = [None]

    async def refresh_progress(label=""):
        now = asyncio.get_event_loop().time()
        if now - last_prog_edit[0] < 3.0: return
        last_prog_edit[0] = now
        total = len(all_items)
        pct   = item_num[0] / total * 100 if total else 0
        bar   = _prog_bar(pct)
        try:
            await prog.edit_text(
                f"⚡ *Extracting…*\n"
                f"📚 `{trunc(course['title'], 45)}`\n\n"
                f"`[{bar}]` *{item_num[0]}/{total}*\n"
                f"🎬 Videos ✅ {vid_ok}  ❌ {vid_fail}\n"
                f"📄 PDFs   ✅ {pdf_ok}  ❌ {pdf_fail}\n"
                + (f"\n`{trunc(label, 42)}`" if label else ""),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception: pass

    for mod, t, m in all_items:
        if cancel_flags.get(uid): break
        while pause_flags.get(uid):
            await asyncio.sleep(0.5)
            if cancel_flags.get(uid): break
        if cancel_flags.get(uid): break

        item_num[0] += 1

        if mod["id"] != current_mod_id[0]:
            current_mod_id[0] = mod["id"]
            mod_title = trunc(mod["title"], 100)
            await safe_send_message(
                tc, dest_chat,
                f"📂 *{mod_title}*\n━━━━━━━━━━━━━━━━━━",
                is_group=is_group,
            )
            if is_group:
                await asyncio.sleep(GROUP_MSG_DELAY)

        cap = (
            f"📚 `{trunc(course['title'], 42)}`\n"
            f"📂 `{trunc(mod['title'], 38)}`\n"
            f"📄 `{trunc(t['title'], 38)}`"
        )

        if m["type"] in ("video_link", "youtube", "vimeo"):
            await refresh_progress(m["name"])
            dl_msg = await context.bot.send_message(
                uid,
                f"⬇️ *Downloading video…*\n`{trunc(m['name'], 50)}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            path = await download_video_ytdlp(m["url"], job_dir, dl_msg, uid)
            if path:
                size  = path.stat().st_size
                up_cb = make_upload_progress_cb(dl_msg, path.name, size)
                try:
                    await dl_msg.edit_text(
                        f"⬆️ *Uploading video…*\n`{trunc(path.name, 42)}`\n📦 `{fmt_size(size)}`",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception: pass
                ok = await safe_send_file(tc, dest_chat, path, caption=cap, progress_cb=up_cb, is_group=is_group)
                path.unlink(missing_ok=True)
                if ok: vid_ok += 1
                else:  vid_fail += 1
            else:
                vid_fail += 1
            try: await dl_msg.delete()
            except Exception: pass

        elif m["type"] in ("pdf", "mp4"):
            await refresh_progress(m["name"])
            fname = re.sub(r"[^\w.\-()]", "_", m["name"])[:80]
            if m["type"] == "pdf" and not fname.lower().endswith(".pdf"):
                fname += ".pdf"
            fpath = job_dir / fname

            async def _dl_prog(pct, done, total, speed, eta):
                now = asyncio.get_event_loop().time()
                if now - last_prog_edit[0] < 2.0: return
                last_prog_edit[0] = now
                bar = _prog_bar(pct)
                try:
                    await prog.edit_text(
                        f"⬇️ *Downloading…*\n"
                        f"`{trunc(fname, 42)}`\n\n"
                        f"`[{bar}]` *{pct:.1f}%*\n"
                        f"📦 `{fmt_size(done)}` / `{fmt_size(total)}`\n"
                        f"⚡ `{fmt_size(int(speed))}/s`  ⏱ `{_eta(eta)}`",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception: pass

            path, err = await download_file_fast(m["url"], fpath, _dl_prog, uid)
            if err and "Stopped" in str(err): break
            if err or not path:
                pdf_fail += 1
                continue

            size   = path.stat().st_size
            up_msg = await context.bot.send_message(
                uid,
                f"⬆️ *Uploading…*\n`{trunc(fname, 42)}`\n📦 `{fmt_size(size)}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            up_cb = make_upload_progress_cb(up_msg, fname, size)
            ok = await safe_send_file(tc, dest_chat, path, caption=cap, progress_cb=up_cb, is_group=is_group)
            path.unlink(missing_ok=True)
            try: await up_msg.delete()
            except Exception: pass
            if ok: pdf_ok += 1
            else:  pdf_fail += 1

    shutil.rmtree(job_dir, ignore_errors=True)

    status_icon = "⛔" if cancel_flags.get(uid) else "✅"
    status_word = "Stopped" if cancel_flags.get(uid) else "Complete"
    await prog.edit_text(
        f"{status_icon} *{status_word}!*\n\n"
        f"📚 `{trunc(course['title'], 55)}`\n\n"
        f"🎬 Videos sent: *{vid_ok}*  ❌ Failed: *{vid_fail}*\n"
        f"📄 PDFs sent:   *{pdf_ok}*  ❌ Failed: *{pdf_fail}*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("Browse Courses 📚", callback_data="browse"),
        ]]),
    )

# ═══════════════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    search_mode.pop(uid, None)
    welcome = (
        "🏎️ *Welcome to Lawctopus Law School Bot!*\n\n"
        "Browse courses, download PDFs & videos, export HTML.\n\n"
        "*Commands:*\n"
        "/start — Home\n/copy — Copy full course\n/upload — Send all links\n"
        "/search — Search\n/setgroup — Set target group for /extract\n"
        "/combine — Offline HTML\n/pause — Pause\n/resume — Resume\n/stop — Stop\n\n"
        "🚀 Tap *Browse All Courses* to begin!"
    )
    jpg = svg_to_jpg_bytes(CAR_SVG)
    if jpg:
        tmp = _TMP / f"lls_car_{uid}.jpg"
        tmp.write_bytes(jpg)
        try:
            await update.message.reply_photo(
                photo=InputFile(open(tmp, "rb"), filename="welcome.jpg"),
                caption=welcome, parse_mode=ParseMode.MARKDOWN, reply_markup=home_kb(),
            )
            tmp.unlink(missing_ok=True)
            return
        except Exception as e:
            logger.warning("Photo send: %s", e)
    await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN, reply_markup=home_kb())


async def cmd_setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    args = context.args or []
    if args:
        try:
            gid = int(args[0])
        except ValueError:
            await update.message.reply_text("Usage: /setgroup -100XXXXXXXXXX")
            return
    else:
        gid = update.effective_chat.id
        if gid == uid:
            await update.message.reply_text(
                "Send /setgroup from inside the target group, or pass the group ID:\n"
                "`/setgroup -100XXXXXXXXXX`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

    user_state.setdefault(uid, {})["group_id"] = gid
    await update.message.reply_text(
        f"✅ Target group set to `{gid}`\n"
        f"Files will be sent to that group with module header messages.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_stop(update, context):
    cancel_flags[update.effective_user.id] = True
    pause_flags[update.effective_user.id]  = False
    await update.message.reply_text("⛔ Stopped.")

async def cmd_pause(update, context):
    pause_flags[update.effective_user.id] = True
    await update.message.reply_text("⏸ Paused. /resume to continue.")

async def cmd_resume(update, context):
    pause_flags[update.effective_user.id] = False
    await update.message.reply_text("▶️ Resumed!")

async def cmd_help(update, context):
    await cmd_start(update, context)

async def cmd_search(update, context):
    query = " ".join(context.args or []).strip()
    if not query:
        search_mode[update.effective_user.id] = True
        await update.message.reply_text(
            "🔍 *Search Courses*\n\nType course name:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="search_cancel")]]),
        )
        return
    await _do_search(update, context, query)

async def handle_text(update, context):
    uid = update.effective_user.id
    if search_mode.pop(uid, False):
        await _do_search(update, context, update.message.text.strip())

async def _do_search(update, context, query: str):
    courses = user_state.get(update.effective_user.id, {}).get("courses") or fetch_all_courses()
    user_state.setdefault(update.effective_user.id, {})["courses"] = courses
    q = query.lower()
    results = [c for c in courses if q in c["title"].lower()]
    if not results:
        await update.message.reply_text(
            f"No courses found for '{query}'.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Browse All", callback_data="browse")]]),
        )
        return
    user_state[update.effective_user.id]["search_results"] = results
    rows = [[InlineKeyboardButton(f"  {trunc(c['title'])}", callback_data=f"sr_{i}")] for i, c in enumerate(results[:20])]
    rows.append([InlineKeyboardButton("Browse All", callback_data="browse")])
    await update.message.reply_text(f"Found {len(results)} course(s):", reply_markup=InlineKeyboardMarkup(rows))

async def cmd_copy(update, context):
    await update.message.reply_text("Loading…")
    courses = fetch_all_courses()
    if not courses: return
    user_state.setdefault(update.effective_user.id, {})["courses"] = courses
    rows = [[InlineKeyboardButton(f"  {trunc(c['title'])}", callback_data=f"cc_{i}")] for i, c in enumerate(courses)]
    rows.append([InlineKeyboardButton("Cancel", callback_data="copy_cancel")])
    await update.message.reply_text("Copy Full Course", reply_markup=InlineKeyboardMarkup(rows))

async def cmd_upload(update, context):
    await update.message.reply_text("Loading…")
    courses = fetch_all_courses()
    if not courses: return
    user_state.setdefault(update.effective_user.id, {})["courses"] = courses
    rows = [[InlineKeyboardButton(f"  {trunc(c['title'])}", callback_data=f"uc_{i}")] for i, c in enumerate(courses)]
    rows.append([InlineKeyboardButton("Cancel", callback_data="upload_cancel")])
    await update.message.reply_text("Send all links from a course.", reply_markup=InlineKeyboardMarkup(rows))

async def cmd_combine(update, context):
    uid = update.effective_user.id
    msg = await update.message.reply_text("Building combined HTML…")
    courses = fetch_all_courses()
    if not courses: return
    sem = asyncio.Semaphore(5)
    async def fetch_one(course):
        async with sem:
            _, info, modules = await asyncio.get_event_loop().run_in_executor(
                None, fetch_course_detail, course["id"])
            mods_out = []
            for mod in modules:
                tops = []
                for t in mod.get("topics", []):
                    media = await extract_media_for_topic(t)
                    video_url = next((m.get("embed","") or m["url"] for m in media if m["type"] in ("video_link","youtube","vimeo")), "")
                    pdf_url   = next((m["url"] for m in media if m["type"] == "pdf"), "")
                    tops.append({"id": t.get("id",""), "title": t.get("title",""),
                                 "video_url": video_url, "pdf_url": pdf_url})
                mods_out.append({"id": mod.get("id",""), "title": mod.get("title",""), "topics": tops})
            return {"id": course["id"], "title": course["title"],
                    "duration": course.get("duration","") or info.get("duration",""),
                    "fee": course.get("fee","") or info.get("fee",""),
                    "link": course.get("link","") or info.get("link",""),
                    "modules": mods_out}
    results = await asyncio.gather(*[fetch_one(c) for c in courses])
    html_text = generate_combined_html([r for r in results if r])
    html_path = HTML_DIR / "lawctopus_all_courses.html"
    async with aiofiles.open(html_path, "w", encoding="utf-8") as f:
        await f.write(html_text)
    tc = await get_tc()
    await safe_send_file(tc, uid, html_path, caption="📚 All Courses")
    html_path.unlink(missing_ok=True)
    await msg.edit_text("✅ Combined HTML sent!")

# ═══════════════════════════════════════════════════════════════════
#  MASTER CALLBACK
# ═══════════════════════════════════════════════════════════════════

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    await q.answer()
    uid = q.from_user.id
    d   = q.data

    if d == "browse":
        courses = fetch_all_courses()
        if not courses: return
        user_state[uid] = {"courses": courses, "page": 0}
        await q.edit_message_text(
            f"📚 All Courses — {len(courses)} available\n\nSelect:",
            reply_markup=courses_kb(courses, 0),
        )

    elif d.startswith("pg_"):
        page    = int(d[3:])
        courses = user_state.get(uid, {}).get("courses", [])
        if not courses: return
        user_state[uid]["page"] = page
        await q.edit_message_text(
            f"📚 All Courses — {len(courses)} available\n\nSelect:",
            reply_markup=courses_kb(courses, page),
        )

    elif d == "search_prompt":
        search_mode[uid] = True
        await q.edit_message_text(
            "🔍 *Search Courses*\n\nType course name:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="search_cancel")]]),
        )
    elif d == "search_cancel":
        search_mode.pop(uid, None)
        await q.edit_message_text("Cancelled.", reply_markup=home_kb())

    elif d.startswith("sr_"):
        idx     = int(d[3:])
        results = user_state.get(uid, {}).get("search_results", [])
        if idx >= len(results): return
        course  = results[idx]
        courses = user_state.get(uid, {}).get("courses") or fetch_all_courses()
        user_state.setdefault(uid, {})["courses"] = courses
        user_state[uid]["course_idx"] = next((i for i, c in enumerate(courses) if c["id"] == course["id"]), 0)
        await _open_course(q, uid, course)

    elif d.startswith("c_"):
        idx     = int(d[2:])
        courses = user_state.get(uid, {}).get("courses", [])
        if idx >= len(courses): return
        user_state[uid]["course_idx"] = idx
        await _open_course(q, uid, courses[idx])

    elif d.startswith("extract_"):
        cidx    = int(d[8:])
        courses = user_state.get(uid, {}).get("courses", [])
        modules = user_state.get(uid, {}).get("modules", [])
        if cidx >= len(courses): return
        course  = courses[cidx]
        if not modules:
            _, _, modules = fetch_course_detail(course["id"])
        if not modules:
            await q.edit_message_text("❌ No modules found.")
            return
        target_group = user_state.get(uid, {}).get("group_id")
        asyncio.create_task(run_extract(q, context, uid, course, modules, target_group))

    elif d.startswith("m_"):
        mod_idx = int(d[2:])
        modules = user_state.get(uid, {}).get("modules", [])
        cidx    = user_state.get(uid, {}).get("course_idx", 0)
        if mod_idx >= len(modules): return
        user_state[uid]["mod_idx"] = mod_idx
        mod    = modules[mod_idx]
        topics = mod.get("topics", [])
        if not topics:
            await q.edit_message_text(
                f"📂 {trunc(mod['title'], 60)}\n\nNo topics.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data=f"c_{cidx}")]]),
            )
            return
        rows = [[InlineKeyboardButton(f" {trunc(t['title'])}", callback_data=f"t_{i}")] for i, t in enumerate(topics)]
        rows.append([InlineKeyboardButton("⬆️ Upload All PDFs", callback_data=f"upload_mod_{mod_idx}"),
                     InlineKeyboardButton("📄 Export HTML",    callback_data="export_html")])
        rows.append([InlineKeyboardButton("← Back", callback_data=f"c_{cidx}")])
        await q.edit_message_text(
            f"📂 *{trunc(mod['title'], 60)}* — {len(topics)} topics",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(rows),
        )

    elif d.startswith("t_"):
        t_idx   = int(d[2:])
        modules = user_state.get(uid, {}).get("modules", [])
        mod_idx = user_state.get(uid, {}).get("mod_idx", 0)
        if mod_idx >= len(modules): return
        topics  = modules[mod_idx].get("topics", [])
        if t_idx >= len(topics): return
        user_state[uid]["t_idx"] = t_idx
        topic = topics[t_idx]
        media = await extract_media_for_topic(topic)
        user_state[uid]["current_media"] = media
        rows  = []
        if not media:
            lnk = topic.get("permalink", "")
            if lnk: rows.append([InlineKeyboardButton("Open in Browser 🌐", url=lnk)])
            rows.append([InlineKeyboardButton("← Back", callback_data=f"m_{mod_idx}")])
            await q.edit_message_text(
                f"📄 {trunc(topic['title'], 60)}\n\nNo content found.",
                reply_markup=InlineKeyboardMarkup(rows),
            )
            return
        lines = [f"📄 *{trunc(topic['title'], 60)}*\n"]
        for i, m in enumerate(media):
            icon = {"video_link":"🎬","youtube":"▶️","vimeo":"🎞️","mp4":"📹","pdf":"📄"}.get(m["type"],"📎")
            lines.append(f"{icon} `{trunc(m['name'], 50)}`")
            if m["type"] in ("video_link","youtube","vimeo"):
                rows.append([InlineKeyboardButton(f"▶️ Watch: {trunc(m['name'],28)}", url=m["url"])])
            elif m["type"] in ("mp4","pdf"):
                rows.append([InlineKeyboardButton(f"⬇️ Download", callback_data=f"dl_{i}")])
        lnk = topic.get("permalink","")
        if lnk: rows.append([InlineKeyboardButton("🌐 Lesson Page", url=lnk)])
        rows.append([InlineKeyboardButton("← Back", callback_data=f"m_{mod_idx}"),
                     InlineKeyboardButton("📄 Export HTML", callback_data="export_html")])
        await q.edit_message_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(rows),
        )

    elif d.startswith("dl_"):
        file_idx = int(d[3:])
        media    = user_state.get(uid, {}).get("current_media", [])
        t_idx    = user_state.get(uid, {}).get("t_idx", 0)
        if file_idx >= len(media): return
        await _download_and_send(q, context, uid, media[file_idx], back_cb=f"t_{t_idx}", label=media[file_idx]["name"])

    elif d.startswith("upload_mod_"):
        mod_idx  = int(d[11:])
        modules  = user_state.get(uid, {}).get("modules", [])
        if mod_idx >= len(modules): return
        mod      = modules[mod_idx]
        all_media = [(t["title"], m) for t in mod.get("topics", [])
                     for m in await extract_media_for_topic(t) if m["type"] in ("pdf","mp4")]
        if not all_media:
            await q.edit_message_text(
                "No downloadable files.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Back", callback_data=f"m_{mod_idx}")]]),
            )
            return
        prog = await q.edit_message_text(f"Uploading {len(all_media)} file(s)…")
        done = failed = 0
        tc = await get_tc()
        for t_title, m in all_media:
            fname = re.sub(r"[^\w.\-()]","_", m["name"])
            fpath = DL_DIR / fname
            path, err = await download_file_fast(m["url"], fpath)
            if err or not path: failed += 1; continue
            cap = f"📂 `{trunc(mod['title'],50)}`\n📄 `{trunc(t_title,50)}`"
            ok  = await safe_send_file(tc, uid, path, caption=cap)
            path.unlink(missing_ok=True)
            if ok: done += 1
            else:  failed += 1
        await prog.edit_text(
            f"Done! ✅ {done}  ❌ {failed}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Back", callback_data=f"m_{mod_idx}"),
                InlineKeyboardButton("All Courses", callback_data="browse"),
            ]]),
        )

    elif d == "export_html":
        courses = user_state.get(uid,{}).get("courses",[])
        cidx    = user_state.get(uid,{}).get("course_idx",0)
        modules = user_state.get(uid,{}).get("modules",[])
        info    = user_state.get(uid,{}).get("course_info",{})
        course  = courses[cidx] if cidx < len(courses) else {}
        if not course or not modules: return
        prog = await q.edit_message_text("Generating HTML…")
        try:
            html_content = generate_course_html(course, modules, info)
            safe_name    = re.sub(r"[^\w\-]","_", course.get("title","course"))[:50]
            html_path    = HTML_DIR / f"{safe_name}.html"
            html_path.write_text(html_content, encoding="utf-8")
            with open(html_path,"rb") as fh:
                await context.bot.send_document(
                    uid, document=InputFile(fh, filename=html_path.name),
                    caption=f"📄 {trunc(course['title'],60)} — Open in browser!",
                )
            html_path.unlink(missing_ok=True)
            await prog.edit_text(
                "HTML sent! ✅",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Back", callback_data=f"c_{cidx}")]]),
            )
        except Exception as e:
            await prog.edit_text(f"Failed: {str(e)[:100]}")

    elif d.startswith("info_"):
        cidx    = int(d[5:])
        courses = user_state.get(uid,{}).get("courses",[])
        info    = user_state.get(uid,{}).get("course_info",{})
        course  = courses[cidx] if cidx < len(courses) else {}
        lines   = [f"📚 *{trunc(course.get('title',''),80)}*\n"]
        if info.get("duration"): lines.append(f"⏱ Duration: {info['duration']}")
        if info.get("fee"):      lines.append(f"💰 Fee: ₹{info['fee']}")
        if info.get("description"): lines.append(f"\n{textwrap.fill(info['description'],80)}")
        await q.edit_message_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("View Modules", callback_data=f"c_{cidx}"),
                InlineKeyboardButton("All Courses",  callback_data="browse"),
            ]]),
        )

    elif d == "copy_cancel":
        await q.edit_message_text("Cancelled.", reply_markup=home_kb())

    elif d.startswith("cc_"):
        cidx = int(d[3:])
        courses = user_state.get(uid,{}).get("courses",[])
        if cidx >= len(courses): return
        course = courses[cidx]
        _, info, modules = fetch_course_detail(course["id"])
        if not modules: return
        user_state.setdefault(uid,{}).update({"copy_cidx":cidx,"copy_modules":modules,"copy_info":info})
        await q.edit_message_text(
            f"📚 {trunc(course['title'],65)}\n\n{len(modules)} modules",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬇️ Download & Upload All", callback_data=f"cd_{cidx}")],
                [InlineKeyboardButton("🔗 Copy All Links",         callback_data=f"cl_{cidx}")],
                [InlineKeyboardButton("Cancel",                    callback_data="copy_cancel")],
            ]),
        )

    elif d.startswith("cd_"):
        cidx    = int(d[3:])
        courses = user_state.get(uid,{}).get("courses",[])
        if cidx >= len(courses): return
        course  = courses[cidx]
        modules = user_state.get(uid,{}).get("copy_modules") or []
        if not modules: _,_,modules = fetch_course_detail(course["id"])
        if not modules: return
        cancel_flags[uid] = False; pause_flags[uid] = False
        prog = await q.edit_message_text("Starting…")
        vid_ok=vid_fail=pdf_ok=pdf_fail=0
        tc = await get_tc()
        for mod in modules:
            if cancel_flags.get(uid): break
            for t in mod.get("topics",[]):
                if cancel_flags.get(uid): break
                while pause_flags.get(uid):
                    await asyncio.sleep(0.5)
                    if cancel_flags.get(uid): break
                cap = f"📚 `{trunc(course['title'],42)}`\n📂 `{trunc(mod['title'],38)}`\n📄 `{trunc(t['title'],38)}`"
                for m in await extract_media_for_topic(t):
                    if m["type"] in ("video_link","youtube","vimeo"):
                        dl_msg = await context.bot.send_message(uid, f"⬇️ Video `{trunc(m['name'],40)}`…", parse_mode=ParseMode.MARKDOWN)
                        path = await download_video_ytdlp(m["url"], DL_DIR, dl_msg, uid)
                        if path:
                            up_cb = make_upload_progress_cb(dl_msg, path.name, path.stat().st_size)
                            ok = await safe_send_file(tc, uid, path, caption=cap, progress_cb=up_cb)
                            path.unlink(missing_ok=True)
                            if ok: vid_ok+=1
                            else:  vid_fail+=1
                        else: vid_fail+=1
                        try: await dl_msg.delete()
                        except: pass
                    elif m["type"] in ("pdf","mp4"):
                        fname = re.sub(r"[^\w.\-()]","_",m["name"])[:80]
                        fpath = DL_DIR / fname
                        path, err = await download_file_fast(m["url"], fpath, uid=uid)
                        if err and "Stopped" in str(err): break
                        if err or not path: pdf_fail+=1; continue
                        up_msg = await context.bot.send_message(uid, f"⬆️ Uploading `{trunc(fname,40)}`…", parse_mode=ParseMode.MARKDOWN)
                        up_cb = make_upload_progress_cb(up_msg, fname, path.stat().st_size)
                        ok = await safe_send_file(tc, uid, path, caption=cap, progress_cb=up_cb)
                        path.unlink(missing_ok=True)
                        try: await up_msg.delete()
                        except: pass
                        if ok: pdf_ok+=1
                        else:  pdf_fail+=1
        await prog.edit_text(
            f"✅ Complete!\n🎬 Videos: {vid_ok}/{vid_fail}  📄 PDFs: {pdf_ok}/{pdf_fail}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Copy Another", callback_data="copy_restart"),
                InlineKeyboardButton("Browse", callback_data="browse"),
            ]]),
        )

    elif d.startswith("cl_"):
        cidx    = int(d[3:])
        courses = user_state.get(uid,{}).get("courses",[])
        if cidx >= len(courses): return
        course  = courses[cidx]
        modules = user_state.get(uid,{}).get("copy_modules") or []
        if not modules: _,_,modules = fetch_course_detail(course["id"])
        if not modules: return
        for mod in modules:
            lines = [f"📂 *{trunc(mod['title'],70)}*\n"]; has=False
            for t in mod.get("topics",[]):
                media = await extract_media_for_topic(t)
                if not media: continue
                lines.append(f"📄 {trunc(t['title'],60)}")
                for m in media:
                    if m["type"] in ("video_link","youtube","vimeo"):
                        lines.append(f"  🎬 [Watch]({m['url']})"); has=True
                    elif m["type"]=="pdf":
                        lines.append(f"  📄 [PDF]({m['url']})"); has=True
                lines.append("")
            if has:
                full = "\n".join(lines)
                for chunk in [full[i:i+4000] for i in range(0,len(full),4000)]:
                    await context.bot.send_message(uid, chunk, disable_web_page_preview=True)

    elif d == "copy_restart":
        courses = user_state.get(uid,{}).get("courses") or fetch_all_courses()
        user_state.setdefault(uid,{})["courses"] = courses
        rows = [[InlineKeyboardButton(f"  {trunc(c['title'])}", callback_data=f"cc_{i}")] for i,c in enumerate(courses)]
        rows.append([InlineKeyboardButton("Cancel", callback_data="copy_cancel")])
        await q.edit_message_text("Copy Full Course", reply_markup=InlineKeyboardMarkup(rows))

    elif d == "upload_cancel":
        await q.edit_message_text("Cancelled.", reply_markup=home_kb())

    elif d.startswith("uc_"):
        cidx    = int(d[3:])
        courses = user_state.get(uid,{}).get("courses",[])
        if cidx >= len(courses): return
        course  = courses[cidx]
        _,_,modules = fetch_course_detail(course["id"])
        if not modules: return
        for mod in modules:
            lines = [f"📂 *{trunc(mod['title'],70)}*\n"]; has=False
            for t in mod.get("topics",[]):
                media = await extract_media_for_topic(t)
                if not media: continue
                lines.append(f"📄 {trunc(t['title'],60)}")
                for m in media:
                    if m["type"] in ("video_link","youtube","vimeo"):
                        lines.append(f"  🎬 [Watch]({m['url']})"); has=True
                    elif m["type"]=="pdf":
                        lines.append(f"  📄 [PDF]({m['url']})"); has=True
                lines.append("")
            if has:
                full = "\n".join(lines)
                for chunk in [full[i:i+4000] for i in range(0,len(full),4000)]:
                    await context.bot.send_message(uid, chunk, disable_web_page_preview=True)

# ═══════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════

async def _open_course(q, uid, course):
    _, info, modules = fetch_course_detail(course["id"])
    cidx = user_state[uid].get("course_idx", 0)
    user_state[uid]["modules"]     = modules
    user_state[uid]["course_info"] = info
    if not modules:
        await q.edit_message_text(
            "No modules found.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Back", callback_data="browse")]]),
        )
        return
    meta = []
    if course.get("duration"): meta.append(f"⏱ {course['duration']}")
    if course.get("fee"):      meta.append(f"💰 ₹{course['fee']}")

    gid = user_state.get(uid, {}).get("group_id")
    group_note = f"\n📤 Extract → Group `{gid}`" if gid else "\n📥 Extract → your private chat"

    rows = [[InlineKeyboardButton(
                f"📂 {trunc(mod['title'])} ({len(mod.get('topics',[]))})",
                callback_data=f"m_{i}",
             )] for i, mod in enumerate(modules)]
    rows.append([
        InlineKeyboardButton("ℹ️ Course Info",  callback_data=f"info_{cidx}"),
        InlineKeyboardButton("📄 Export HTML", callback_data="export_html"),
    ])
    rows.append([
        InlineKeyboardButton("🚀 Extract Full Course ⬇️", callback_data=f"extract_{cidx}"),
    ])
    rows.append([InlineKeyboardButton("← Back", callback_data="browse")])

    await q.edit_message_text(
        f"📚 *{trunc(course['title'],70)}*\n"
        f"{' | '.join(meta)}"
        f"{group_note}\n\n"
        f"{len(modules)} modules — browse below or tap *Extract* to download all.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _download_and_send(q, context, uid, finfo, back_cb, label):
    fname = re.sub(r"[^\w.\-()]","_", finfo["name"])
    fpath = DL_DIR / fname
    prog  = await q.edit_message_text(f"⬇️ Downloading `{trunc(label,45)}`…", parse_mode=ParseMode.MARKDOWN)
    path, err = await download_file_fast(finfo["url"], fpath, uid=uid)
    if err or not path:
        await prog.edit_text(
            f"❌ Failed: {(err or 'unknown')[:120]}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Back", callback_data=back_cb)]]),
        )
        return
    size_mb = path.stat().st_size / (1024 * 1024)
    tc  = await get_tc()
    up_cb = make_upload_progress_cb(prog, fname, path.stat().st_size)
    try:
        await prog.edit_text(
            f"⬆️ Uploading `{trunc(fname,42)}`…\n📦 `{fmt_size(int(size_mb*1048576))}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception: pass
    ok = await safe_send_file(tc, uid, path, caption=f"`{fname}`", progress_cb=up_cb)
    path.unlink(missing_ok=True)
    if ok:
        await prog.edit_text(
            f"✅ Sent! `{trunc(fname,45)}` ({size_mb:.1f} MB)",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Back", callback_data=back_cb)]]),
        )
    else:
        await prog.edit_text(
            "❌ Upload failed.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Back", callback_data=back_cb)]]),
        )

# ═══════════════════════════════════════════════════════════════════
#  STARTUP
# ═══════════════════════════════════════════════════════════════════

async def post_init(app):
    await get_tc()
    from telegram import BotCommand
    await app.bot.set_my_commands([
        BotCommand("start",    "Home + animated car"),
        BotCommand("setgroup", "Set target group for /extract"),
        BotCommand("copy",     "Copy full course"),
        BotCommand("upload",   "Send all links"),
        BotCommand("search",   "Search courses"),
        BotCommand("combine",  "Offline HTML"),
        BotCommand("pause",    "Pause download"),
        BotCommand("resume",   "Resume download"),
        BotCommand("stop",     "Stop download"),
        BotCommand("help",     "Help"),
    ])


async def post_shutdown(app):
    global _tc
    if _tc and _tc.is_connected():
        await _tc.disconnect()
        logger.info("Telethon disconnected")


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(CommandHandler("setgroup", cmd_setgroup))
    app.add_handler(CommandHandler("copy",     cmd_copy))
    app.add_handler(CommandHandler("upload",   cmd_upload))
    app.add_handler(CommandHandler("search",   cmd_search))
    app.add_handler(CommandHandler("combine",  cmd_combine))
    app.add_handler(CommandHandler("stop",     cmd_stop))
    app.add_handler(CommandHandler("pause",    cmd_pause))
    app.add_handler(CommandHandler("resume",   cmd_resume))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Bot starting…")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
