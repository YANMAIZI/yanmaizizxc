"""
config.py — Конфигурация бота.
Приоритет для 24/7-автопостинга:
  1) официальный API + refresh token;
  2) cookies только как legacy fallback.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────
BOT_TOKEN = os.getenv('BOT_TOKEN', 'ВСТАВЬ_ТОКЕН_СЮДА')

# ── Legacy cookies (аварийный fallback) ───────────────────────
YT_COOKIES_FILE = 'yt_cookies.txt'
TT_COOKIES_FILE = 'tt_cookies.txt'

# ── Рабочие папки и state ─────────────────────────────────────
OUTPUT_DIR = 'clips_output'
SETTINGS_FILE = 'user_settings.json'
TOKENS_DIR = os.getenv('TOKENS_DIR', 'tokens')
YT_OAUTH_TOKEN_FILE = os.path.join(TOKENS_DIR, 'youtube_token.json')
TT_OAUTH_TOKEN_FILE = os.path.join(TOKENS_DIR, 'tiktok_token.json')

# ── Нарезка ───────────────────────────────────────────────────
CLIP_MIN_SEC = 45
CLIP_MAX_SEC = 55
MAX_FILE_MB = 50

# ── YouTube ───────────────────────────────────────────────────
AUTO_HASHTAGS = '#shorts #нарезка #короткиеролики #рекомендации'
YT_TITLE_MAX = 95
YT_PRIVACY = os.getenv('YT_PRIVACY', 'public')
YT_CLIENT_ID = os.getenv('YT_CLIENT_ID', '')
YT_CLIENT_SECRET = os.getenv('YT_CLIENT_SECRET', '')
YT_REFRESH_TOKEN = os.getenv('YT_REFRESH_TOKEN', '')

# ── TikTok ────────────────────────────────────────────────────
TT_HASHTAGS = '#fyp #viral #shorts #нарезка #рекомендации'
TT_TITLE_MAX = 100
TT_API_BASE_URL = os.getenv('TT_API_BASE_URL', 'https://open.tiktokapis.com')
TT_CLIENT_KEY = os.getenv('TT_CLIENT_KEY', '')
TT_CLIENT_SECRET = os.getenv('TT_CLIENT_SECRET', '')
TT_REFRESH_TOKEN = os.getenv('TT_REFRESH_TOKEN', '')
TT_ACCESS_TOKEN = os.getenv('TT_ACCESS_TOKEN', '')
TT_OPEN_ID = os.getenv('TT_OPEN_ID', '')
TT_DIRECT_POST_ENABLED = os.getenv('TT_DIRECT_POST_ENABLED', '1') == '1'

# ── Стабильность ──────────────────────────────────────────────
SEND_TIMEOUT = 120
SEND_RETRIES = 3
DOWNLOAD_TIMEOUT = 600
MAX_CONCURRENT_JOBS = 3

# ── Legacy Playwright uploader tuning ─────────────────────────
PW_PAGE_LOAD_MS = 30000
PW_VIDEO_PROCESS_MS = 60000
PW_FILE_CHOOSER_MS = 60000
PW_SELECTOR_MS = 10000
