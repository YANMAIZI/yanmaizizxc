"""
config.py — Конфигурация бота (только cookies, без OAuth сервера)
Единственное что нужно заполнить — BOT_TOKEN в файле .env
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВСТАВЬ_ТОКЕН_СЮДА")

# ── Файлы cookies (положи рядом с ботом) ─────────────────────
YT_COOKIES_FILE = "yt_cookies.txt"   # экспорт из браузера на youtube.com
TT_COOKIES_FILE = "tt_cookies.txt"   # экспорт из браузера на tiktok.com

# ── Рабочие папки ─────────────────────────────────────────────
OUTPUT_DIR    = "clips_output"
SETTINGS_FILE = "user_settings.json"

# ── Нарезка ───────────────────────────────────────────────────
# Длительность каждого клипа выбирается случайно в этом диапазоне (секунды)
CLIP_MIN_SEC = 45
CLIP_MAX_SEC = 55
MAX_FILE_MB  = 50     # лимит отправки в Telegram

# ── YouTube ───────────────────────────────────────────────────
AUTO_HASHTAGS = "#shorts #нарезка #короткиеролики #рекомендации"
YT_TITLE_MAX  = 95
YT_PRIVACY    = "public"   # public / private / unlisted

# ── TikTok ────────────────────────────────────────────────────
TT_HASHTAGS   = "#fyp #viral #shorts #нарезка #рекомендации"
TT_TITLE_MAX  = 100

# ── Стабильность ──────────────────────────────────────────────
SEND_TIMEOUT        = 120
SEND_RETRIES        = 3
DOWNLOAD_TIMEOUT    = 600
MAX_CONCURRENT_JOBS = 3

# ── Playwright (загрузка на платформы), миллисекунды ───────────
PW_PAGE_LOAD_MS     = 30000   # навигация / загрузка страницы
PW_VIDEO_PROCESS_MS = 60000   # обработка видео после выбора файла
PW_FILE_CHOOSER_MS  = 60000   # ожидание диалога выбора файла
PW_SELECTOR_MS      = 10000   # клики по элементам
