"""
yt_clipper_bot.py — YT Clipper Bot
Нарезает YouTube-видео на клипы 9:16 и постит на YouTube + TikTok.

Что нужно для запуска:
  1. BOT_TOKEN в файле .env
  2. yt_cookies.txt  — cookies с youtube.com (для авто-постинга YouTube)
  3. tt_cookies.txt  — cookies с tiktok.com  (для авто-постинга TikTok)
  4. ffmpeg в PATH   — для нарезки видео

Запуск: python yt_clipper_bot.py
"""

import os, re, json, asyncio, subprocess, shutil, traceback, random
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)
from telegram.error import TimedOut, NetworkError, RetryAfter

from config import (
    BOT_TOKEN, OUTPUT_DIR, MAX_FILE_MB, CLIP_MIN_SEC, CLIP_MAX_SEC,
    AUTO_HASHTAGS, YT_TITLE_MAX, TT_HASHTAGS, TT_TITLE_MAX,
    SEND_TIMEOUT, SEND_RETRIES, DOWNLOAD_TIMEOUT, MAX_CONCURRENT_JOBS,
    SETTINGS_FILE, YT_COOKIES_FILE, TT_COOKIES_FILE,
)
from yt_uploader import (
    upload_video,
    get_channel_info,
    cookie_diagnosis as yt_cookie_diagnosis,
    is_configured as yt_ok,
    is_logged_in as yt_auth,
)
from tt_uploader import upload_to_tiktok, get_tiktok_user_info, is_configured as tt_ok

# ─────────────────────────────────────────────────────
FFMPEG  = "ffmpeg"
FFPROBE = "ffprobe"

POSITIONS = {
    "tl": ("Сверху-лево",   "10",      "10"),
    "tc": ("Сверху-центр",  "(W-w)/2", "10"),
    "tr": ("Сверху-право",  "W-w-10",  "10"),
    "ml": ("Центр-лево",    "10",      "(H-h)/2"),
    "mc": ("По центру",     "(W-w)/2", "(H-h)/2"),
    "mr": ("Центр-право",   "W-w-10",  "(H-h)/2"),
    "bl": ("Снизу-лево",    "10",      "H-h-10"),
    "bc": ("Снизу-центр",   "(W-w)/2", "H-h-10"),
    "br": ("Снизу-право",   "W-w-10",  "H-h-10"),
}

waiting_logo:  set = set()
waiting_wm_pct: set = set()  # ждём число 10–100 для размера логотипа
active_jobs:   set = set()
job_semaphore: asyncio.Semaphore = None   # init в main()


def _clamp_wm_pct(v) -> int:
    try:
        return max(10, min(100, int(v)))
    except (TypeError, ValueError):
        return 20


def wm_percent_keyboard(prefix: str) -> InlineKeyboardMarkup:
    """prefix: ob_wm или s_wm → callback ob_wm:10 / s_wm:10 …"""
    rows = []
    for chunk in ([10, 20, 30, 40, 50], [60, 70, 80, 90, 100]):
        rows.append(
            [InlineKeyboardButton(f"{x}%", callback_data=f"{prefix}:{x}") for x in chunk]
        )
    return InlineKeyboardMarkup(rows)


# ═══════════════════════════════════════════════════════
#  НАСТРОЙКИ ПОЛЬЗОВАТЕЛЯ
# ═══════════════════════════════════════════════════════

def load_all() -> dict:
    if Path(SETTINGS_FILE).exists():
        try:
            return json.loads(Path(SETTINGS_FILE).read_text("utf-8"))
        except Exception:
            pass
    return {}

def save_all(data: dict):
    tmp = SETTINGS_FILE + ".tmp"
    Path(tmp).write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, SETTINGS_FILE)

def get_user(uid: int) -> dict:
    data = load_all()
    key  = str(uid)
    if key not in data:
        data[key] = {
            "setup_done":     False,
            "watermark_path": None,
            "watermark_pos":  "br",
            "watermark_size": 20,
            "wm_type":        None,
            "post_youtube":   False,
            "post_tiktok":    False,
        }
        save_all(data)
    u = data[key]
    u["watermark_size"] = _clamp_wm_pct(u.get("watermark_size", 20))
    return u

def save_user(uid: int, s: dict):
    data = load_all()
    data[str(uid)] = s
    save_all(data)


# ═══════════════════════════════════════════════════════
#  СТАТУС ПЛАТФОРМ
# ═══════════════════════════════════════════════════════

def yt_status() -> str:
    if not yt_ok():   return "не настроен"
    if not yt_auth(): return "нужен вход"
    return "готов"

def tt_status() -> str:
    if not tt_ok(): return "нет cookies"
    return "готов"

def yt_ready() -> bool:
    return yt_ok() and yt_auth()

def tt_ready() -> bool:
    return tt_ok()


# ═══════════════════════════════════════════════════════
#  ОНБОРДИНГ
# ═══════════════════════════════════════════════════════

async def start_onboarding(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    s   = get_user(uid)
    s["setup_done"] = False
    save_user(uid, s)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Начать настройку", callback_data="ob:start")],
    ])
    await update.message.reply_text(
        "Добро пожаловать в YT Clipper!\n\n"
        "Нарезаю видео на клипы 9:16\n"
        "и публикую на YouTube и TikTok.\n\n"
        f"YouTube: {yt_status()}\n"
        f"TikTok:  {tt_status()}\n\n"
        "Нажми чтобы начать:",
        reply_markup=kb
    )


async def ob_step_youtube(query):
    if yt_ready():
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("YouTube готов, далее", callback_data="ob:step_tiktok")],
        ])
        await query.edit_message_text(
            f"YouTube уже подключён!\nСтатус: {yt_status()}",
            reply_markup=kb
        )
    else:
        yt_file_ok = "OK" if Path(YT_COOKIES_FILE).exists() else "НЕТ"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Как получить cookies?",   callback_data="ob:yt_help")],
            [InlineKeyboardButton("Я положил yt_cookies.txt", callback_data="ob:yt_check")],
            [InlineKeyboardButton("Пропустить YouTube",       callback_data="ob:step_tiktok")],
        ])
        await query.edit_message_text(
            "Подключение YouTube\n\n"
            "Нужен один файл: yt_cookies.txt\n\n"
            f"Файл найден: {yt_file_ok}\n\n"
            "Нажми «Как получить cookies?» для инструкции.",
            reply_markup=kb
        )


async def ob_step_tiktok(query):
    if tt_ready():
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("TikTok готов, далее", callback_data="ob:step_logo")],
        ])
        await query.edit_message_text(
            f"TikTok уже подключён!\nСтатус: {tt_status()}",
            reply_markup=kb
        )
    else:
        tt_file_ok = "OK" if Path(TT_COOKIES_FILE).exists() else "НЕТ"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Как получить cookies?",    callback_data="ob:tt_help")],
            [InlineKeyboardButton("Я положил tt_cookies.txt", callback_data="ob:tt_check")],
            [InlineKeyboardButton("Пропустить TikTok",        callback_data="ob:step_logo")],
        ])
        await query.edit_message_text(
            "Подключение TikTok\n\n"
            "Нужен один файл: tt_cookies.txt\n\n"
            f"Файл найден: {tt_file_ok}\n\n"
            "Нажми «Как получить cookies?» для инструкции.",
            reply_markup=kb
        )


async def ob_step_logo(query, uid: int):
    waiting_logo.add(uid)
    s    = get_user(uid)
    logo = "есть" if s.get("watermark_path") else "нет"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Пропустить", callback_data="ob:logo_skip")],
    ])
    await query.edit_message_text(
        f"Логотип (водяной знак)\n\nСейчас: {logo}\n\n"
        "Отправь PNG или JPG прямо сейчас.\n"
        "Будет появляться на каждом клипе.\n"
        "Или нажми Пропустить.",
        reply_markup=kb
    )


async def ob_done(query, uid: int):
    s = get_user(uid)
    s["setup_done"]   = True
    s["post_youtube"] = yt_ready()
    s["post_tiktok"]  = tt_ready()
    save_user(uid, s)
    pos  = POSITIONS.get(s.get("watermark_pos", "br"), ("?",))[0]
    logo = "есть" if s.get("watermark_path") else "нет"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Настройки", callback_data="open_settings")],
    ])
    await query.edit_message_text(
        "Всё готово!\n\n"
        f"YouTube:  {yt_status()}\n"
        f"TikTok:   {tt_status()}\n"
        f"Логотип:  {logo}  ({pos})\n\n"
        "Отправь ссылку на YouTube-видео:",
        reply_markup=kb
    )


# ═══════════════════════════════════════════════════════
#  НАСТРОЙКИ
# ═══════════════════════════════════════════════════════

async def show_settings(target, uid: int, edit: bool = False):
    s   = get_user(uid)
    pos = POSITIONS.get(s.get("watermark_pos", "br"), ("?",))[0]
    logo = "есть" if s.get("watermark_path") else "нет"
    wms = s.get("watermark_size", 20)
    ayt = "вкл" if s.get("post_youtube") else "выкл"
    att = "вкл" if s.get("post_tiktok")  else "выкл"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"YouTube: {yt_status()}",  callback_data="s:yt")],
        [InlineKeyboardButton(f"TikTok:  {tt_status()}",  callback_data="s:tt")],
        [InlineKeyboardButton(f"Авто YouTube: {ayt}",     callback_data="s:tog_yt"),
         InlineKeyboardButton(f"Авто TikTok: {att}",      callback_data="s:tog_tt")],
        [InlineKeyboardButton(f"Логотип: {logo}",         callback_data="s:logo")],
        [InlineKeyboardButton(f"Позиция: {pos}",          callback_data="s:pos")],
        [InlineKeyboardButton(f"Размер лого: {wms}%",    callback_data="s:wm_size")],
        [InlineKeyboardButton("Закрыть",                  callback_data="s:close")],
    ])
    text = (
        "Настройки\n\n"
        f"YouTube: {yt_status()}\n"
        f"TikTok:  {tt_status()}\n"
        f"Логотип: {logo}  Позиция: {pos}  Размер: {wms}%"
    )
    if edit:
        await target.edit_message_text(text, reply_markup=kb)
    else:
        await target.reply_text(text, reply_markup=kb)


# ═══════════════════════════════════════════════════════
#  КОМАНДЫ
# ═══════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    s   = get_user(uid)
    if s.get("setup_done"):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Настройки", callback_data="open_settings")],
        ])
        await update.message.reply_text(
            "YT Clipper Bot\n\n"
            f"YouTube: {yt_status()}\n"
            f"TikTok:  {tt_status()}\n\n"
            "Отправь ссылку на YouTube-видео:",
            reply_markup=kb
        )
    else:
        await start_onboarding(update, ctx)

async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await show_settings(update.message, update.effective_user.id)

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    s   = get_user(uid)
    yt_file = "OK" if Path(YT_COOKIES_FILE).exists() else "НЕТ"
    tt_file = "OK" if Path(TT_COOKIES_FILE).exists() else "НЕТ"
    yt_extra = ""
    if yt_ok() and not yt_auth():
        yt_extra = f"\nYouTube cookies: {yt_cookie_diagnosis()}"
    await update.message.reply_text(
        "Статус\n\n"
        f"YouTube:       {yt_status()}\n"
        f"TikTok:        {tt_status()}\n"
        f"Авто YouTube:  {'вкл' if s.get('post_youtube') else 'выкл'}\n"
        f"Авто TikTok:   {'вкл' if s.get('post_tiktok')  else 'выкл'}\n\n"
        "Файлы:\n"
        f"yt_cookies.txt: {yt_file}\n"
        f"tt_cookies.txt: {tt_file}"
        f"{yt_extra}"
    )

async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    s   = get_user(uid)
    s["setup_done"] = False
    save_user(uid, s)
    await start_onboarding(update, ctx)


# ═══════════════════════════════════════════════════════
#  CALLBACK HANDLER
# ═══════════════════════════════════════════════════════

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = update.effective_user.id
    s    = get_user(uid)
    data = query.data

    # ── Онбординг ─────────────────────────────────────────────
    if data == "ob:start":
        await ob_step_youtube(query)

    elif data == "ob:yt_help":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Назад", callback_data="ob:start")],
        ])
        await query.edit_message_text(
            "Как получить yt_cookies.txt\n\n"
            "1. Установи расширение в Chrome или Edge:\n"
            "   Get cookies.txt LOCALLY\n"
            "   (найди в магазине расширений)\n\n"
            "2. Зайди на youtube.com и на studio.youtube.com\n"
            "   (желательно экспорт cookies на странице Studio,\n"
            "   чтобы попали google.com cookies)\n\n"
            "3. Нажми на иконку расширения\n"
            "   Нажми Export\n\n"
            "4. Сохрани файл как yt_cookies.txt\n"
            "   Положи рядом с ботом\n\n"
            "Не выходи из YouTube в браузере —\n"
            "иначе cookies перестанут работать.",
            reply_markup=kb
        )

    elif data == "ob:yt_check":
        if yt_ready():
            s["post_youtube"] = True
            save_user(uid, s)
            await query.answer("YouTube подключён!", show_alert=True)
            await ob_step_tiktok(query)
        else:
            if not Path(YT_COOKIES_FILE).exists():
                msg = "Файл yt_cookies.txt не найден рядом с ботом!"
            else:
                msg = (
                    "В yt_cookies.txt нужны cookies YouTube и Google. "
                    "Экспортируй со страницы studio.youtube.com."
                )
            await query.answer(msg, show_alert=True)

    elif data == "ob:step_tiktok":
        await ob_step_tiktok(query)

    elif data == "ob:tt_help":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Назад", callback_data="ob:step_tiktok")],
        ])
        await query.edit_message_text(
            "Как получить tt_cookies.txt\n\n"
            "1. Установи расширение в Chrome или Edge:\n"
            "   Get cookies.txt LOCALLY\n"
            "   (найди в магазине расширений)\n\n"
            "2. Зайди на tiktok.com\n"
            "   Убедись что ты залогинен\n\n"
            "3. Нажми на иконку расширения\n"
            "   Нажми Export\n\n"
            "4. Сохрани файл как tt_cookies.txt\n"
            "   Положи рядом с ботом\n\n"
            "Не выходи из TikTok в браузере —\n"
            "иначе cookies перестанут работать.",
            reply_markup=kb
        )

    elif data == "ob:tt_check":
        if tt_ready():
            s["post_tiktok"] = True
            save_user(uid, s)
            await query.answer("TikTok подключён!", show_alert=True)
            await ob_step_logo(query, uid)
        else:
            await query.answer(
                "Файл tt_cookies.txt не найден рядом с ботом!",
                show_alert=True
            )

    elif data == "ob:step_logo":
        await ob_step_logo(query, uid)

    elif data == "ob:logo_skip":
        waiting_logo.discard(uid)
        await ob_done(query, uid)

    elif data.startswith("ob_pos:"):
        pos_key = data[7:]
        s["watermark_pos"] = pos_key
        save_user(uid, s)
        pos_name = POSITIONS[pos_key][0]
        await query.edit_message_text(
            f"Размер логотипа (10–100% ширины кадра)?\n\nПозиция: {pos_name}",
            reply_markup=wm_percent_keyboard("ob_wm"),
        )

    elif data.startswith("ob_wm:"):
        s["watermark_size"] = _clamp_wm_pct(data[6:])
        save_user(uid, s)
        await ob_done(query, uid)

    elif data.startswith("ob_size:"):
        s["watermark_size"] = _clamp_wm_pct(data[8:])
        save_user(uid, s)
        await ob_done(query, uid)

    elif data == "open_settings":
        waiting_wm_pct.discard(uid)
        await show_settings(query, uid, edit=True)

    elif data == "s:close":
        waiting_wm_pct.discard(uid)
        await query.edit_message_text("Настройки сохранены\n\nОтправь ссылку на YouTube:")

    # ── YouTube ───────────────────────────────────────────────
    elif data == "s:yt":
        yt_file = "OK" if Path(YT_COOKIES_FILE).exists() else "НЕТ"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Как получить cookies?",     callback_data="ob:yt_help")],
            [InlineKeyboardButton("Я положил yt_cookies.txt",  callback_data="ob:yt_check")],
            [InlineKeyboardButton("Назад",                     callback_data="open_settings")],
        ])
        await query.edit_message_text(
            f"YouTube\nСтатус: {yt_status()}\n\nФайл yt_cookies.txt: {yt_file}",
            reply_markup=kb
        )

    elif data == "s:tog_yt":
        if not yt_ready():
            await query.answer("Сначала подключи YouTube!", show_alert=True)
            return
        s["post_youtube"] = not s.get("post_youtube", False)
        save_user(uid, s)
        await show_settings(query, uid, edit=True)

    # ── TikTok ────────────────────────────────────────────────
    elif data == "s:tt":
        tt_file = "OK" if Path(TT_COOKIES_FILE).exists() else "НЕТ"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("Как получить cookies?",     callback_data="ob:tt_help")],
            [InlineKeyboardButton("Я положил tt_cookies.txt",  callback_data="ob:tt_check")],
            [InlineKeyboardButton("Назад",                     callback_data="open_settings")],
        ])
        await query.edit_message_text(
            f"TikTok\nСтатус: {tt_status()}\n\nФайл tt_cookies.txt: {tt_file}",
            reply_markup=kb
        )

    elif data == "s:tog_tt":
        if not tt_ready():
            await query.answer("Сначала подключи TikTok!", show_alert=True)
            return
        s["post_tiktok"] = not s.get("post_tiktok", False)
        save_user(uid, s)
        await show_settings(query, uid, edit=True)

    # ── Логотип ───────────────────────────────────────────────
    elif data == "s:logo":
        waiting_logo.add(uid)
        await query.edit_message_text("Отправь PNG или JPG для логотипа:")

    elif data == "s:pos":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("↖", callback_data="ob_pos:tl"),
             InlineKeyboardButton("⬆", callback_data="ob_pos:tc"),
             InlineKeyboardButton("↗", callback_data="ob_pos:tr")],
            [InlineKeyboardButton("⬅", callback_data="ob_pos:ml"),
             InlineKeyboardButton("✛", callback_data="ob_pos:mc"),
             InlineKeyboardButton("➡", callback_data="ob_pos:mr")],
            [InlineKeyboardButton("↙", callback_data="ob_pos:bl"),
             InlineKeyboardButton("⬇", callback_data="ob_pos:bc"),
             InlineKeyboardButton("↘", callback_data="ob_pos:br")],
        ])
        await query.edit_message_text("Где разместить логотип?", reply_markup=kb)

    elif data == "s:wm_size":
        waiting_wm_pct.add(uid)
        await query.edit_message_text(
            "Размер логотипа: доля ширины кадра 10–100%.\n"
            "Выбери процент или отправь число сообщением (например 37).",
            reply_markup=wm_percent_keyboard("s_wm"),
        )

    elif data.startswith("s_wm:"):
        waiting_wm_pct.discard(uid)
        s["watermark_size"] = _clamp_wm_pct(data[5:])
        save_user(uid, s)
        await show_settings(query, uid, edit=True)


# ═══════════════════════════════════════════════════════
#  МЕДИА (логотип)
# ═══════════════════════════════════════════════════════

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in waiting_logo:
        return
    waiting_logo.discard(uid)
    photo = update.message.photo[-1]
    file  = await photo.get_file()
    path  = f"watermarks/{uid}.jpg"
    Path("watermarks").mkdir(exist_ok=True)
    await file.download_to_drive(path)
    s = get_user(uid)
    s["watermark_path"] = path
    s["wm_type"]        = "image"
    save_user(uid, s)
    await _ask_pos(update.message)

async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in waiting_logo:
        return
    doc  = update.message.document
    name = (doc.file_name or "").lower()
    if not any(name.endswith(e) for e in (".png", ".jpg", ".jpeg", ".gif")):
        await update.message.reply_text("Отправь PNG, JPG или GIF")
        return
    waiting_logo.discard(uid)
    file = await doc.get_file()
    ext  = name.rsplit(".", 1)[-1]
    path = f"watermarks/{uid}.{ext}"
    Path("watermarks").mkdir(exist_ok=True)
    await file.download_to_drive(path)
    s = get_user(uid)
    s["watermark_path"] = path
    s["wm_type"]        = "gif" if ext == "gif" else "image"
    save_user(uid, s)
    await _ask_pos(update.message)

async def _ask_pos(msg):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("↖", callback_data="ob_pos:tl"),
         InlineKeyboardButton("⬆", callback_data="ob_pos:tc"),
         InlineKeyboardButton("↗", callback_data="ob_pos:tr")],
        [InlineKeyboardButton("⬅", callback_data="ob_pos:ml"),
         InlineKeyboardButton("✛", callback_data="ob_pos:mc"),
         InlineKeyboardButton("➡", callback_data="ob_pos:mr")],
        [InlineKeyboardButton("↙", callback_data="ob_pos:bl"),
         InlineKeyboardButton("⬇", callback_data="ob_pos:bc"),
         InlineKeyboardButton("↘", callback_data="ob_pos:br")],
    ])
    await msg.reply_text("Логотип сохранён! Где разместить?", reply_markup=kb)


# ═══════════════════════════════════════════════════════
#  ОБРАБОТКА ССЫЛОК
# ═══════════════════════════════════════════════════════

YT_REGEX = re.compile(
    r"(https?://)?(www\.)?"
    r"(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)"
    r"[\w\-]+"
)

async def handle_wm_size_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    v = _clamp_wm_pct(text)
    s = get_user(uid)
    s["watermark_size"] = v
    save_user(uid, s)
    waiting_wm_pct.discard(uid)
    await update.message.reply_text(
        f"Размер логотипа: {v}%",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Настройки", callback_data="open_settings")],
        ]),
    )


async def handle_text_dispatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in waiting_wm_pct:
        text = (update.message.text or "").strip()
        if text.isdigit():
            await handle_wm_size_text(update, ctx)
        else:
            waiting_wm_pct.discard(uid)
            await handle_url(update, ctx)
        return
    await handle_url(update, ctx)


async def handle_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    s    = get_user(uid)
    text = update.message.text.strip()

    if not s.get("setup_done"):
        await update.message.reply_text("Сначала нажми /start для настройки")
        return

    match = YT_REGEX.search(text)
    if not match:
        return

    if uid in active_jobs:
        await update.message.reply_text("Подожди — предыдущее видео ещё обрабатывается.")
        return

    url = match.group(0)
    if not url.startswith("http"):
        url = "https://" + url

    async with job_semaphore:
        active_jobs.add(uid)
        try:
            await _process_video(update, uid, s, url)
        finally:
            active_jobs.discard(uid)


async def _process_video(update: Update, uid: int, s: dict, url: str):
    msg      = await update.message.reply_text("Проверяю видео...")
    work_dir = Path(OUTPUT_DIR) / str(uid) / "work"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        video_title = get_title(url)
        await msg.edit_text(f"Скачиваю...\n{video_title[:60]}")

        raw = str(work_dir / "raw.mp4")
        dl  = subprocess.run(
            ["yt-dlp",
             "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
             "--merge-output-format", "mp4",
             "-o", raw, "--no-playlist", "--no-warnings",
             "--retries", "3", "--fragment-retries", "3",
             url],
            capture_output=True, text=True, timeout=DOWNLOAD_TIMEOUT,
        )
        if dl.returncode != 0 or not Path(raw).exists():
            await msg.edit_text(f"Не удалось скачать\n\n{diagnose(dl.stderr)}")
            return

        await msg.edit_text("Нарезаю на части...")
        clips = cut_and_crop(raw, work_dir, s)
        if not clips:
            await msg.edit_text("ffmpeg не создал клипы.")
            return

        total = len(clips)
        w = "часть" if total == 1 else "части" if total in [2,3,4] else "частей"
        await msg.edit_text(f"Отправляю {total} {w}...\n{video_title[:60]}")

        do_yt = s.get("post_youtube") and yt_ready()
        do_tt = s.get("post_tiktok")  and tt_ready()
        sent  = 0

        if s.get("post_youtube") and not yt_ready():
            await update.message.reply_text(
                "YouTube не постится: статус «нужен вход». "
                "В yt_cookies.txt должны быть cookies и с youtube.com, и с google.com. "
                "Открой в браузере https://studio.youtube.com (без редиректа на вход), "
                "затем экспортируй cookies расширением в yt_cookies.txt и перезапусти бота. "
                "Пока это не исправлено, загрузка в YouTube пропускается."
            )

        for i, clip_path in enumerate(clips, 1):
            if not Path(clip_path).exists():
                continue
            if os.path.getsize(clip_path) / 1024 / 1024 > MAX_FILE_MB:
                await update.message.reply_text(f"Часть {i} слишком большая, пропускаю")
                continue

            yt_title = make_title(video_title, i, total, YT_TITLE_MAX)
            caption  = f"Часть {i} из {total}\n{video_title[:70]}"

            ok = await send_safe(update.get_bot(), update.effective_chat.id, clip_path, caption)
            if not ok:
                await update.message.reply_text(f"Часть {i} не отправилась. Остановка.")
                break
            sent += 1

            if do_yt:
                sm = await update.message.reply_text(f"Загружаю часть {i} на YouTube...")
                desc = f"{yt_title}\n\n{AUTO_HASHTAGS}"
                loop = asyncio.get_event_loop()
                res  = await loop.run_in_executor(
                    None, lambda p=clip_path, t=yt_title, d=desc: upload_video(uid, p, t, d)
                )
                if res:
                    await sm.edit_text(f"YouTube часть {i}: {res['url']}")
                else:
                    await sm.edit_text(f"YouTube часть {i}: ошибка. Попробуй позже.")

            if do_tt:
                sm2 = await update.message.reply_text(f"Загружаю часть {i} в TikTok...")
                tt_tags  = [t.lstrip("#") for t in TT_HASHTAGS.split() if t.startswith("#")]
                tt_title = make_title(video_title, i, total, TT_TITLE_MAX)
                loop = asyncio.get_event_loop()
                res2 = await loop.run_in_executor(
                    None, lambda p=clip_path, t=tt_title: upload_to_tiktok(uid, p, t, tags=tt_tags)
                )
                if res2:
                    await sm2.edit_text(f"TikTok часть {i}: опубликовано")
                else:
                    await sm2.edit_text(f"TikTok часть {i}: ошибка. Попробуй позже или проверь tt_cookies.txt.")

            if i < total:
                await asyncio.sleep(2)

        w2 = "часть" if sent == 1 else "части" if sent in [2,3,4] else "частей"
        await msg.edit_text(f"Готово! {sent} {w2}\n{video_title[:80]}")

    except subprocess.TimeoutExpired:
        await msg.edit_text("Слишком долго. Попробуй ещё раз.")
    except Exception as e:
        print(traceback.format_exc())
        await msg.edit_text(f"Ошибка: {str(e)[:300]}")
    finally:
        if work_dir.exists():
            shutil.rmtree(work_dir)


# ═══════════════════════════════════════════════════════
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════════════

CROP_SCALE = (
    "crop=if(gt(iw\\,ih*9/16)\\,ih*9/16\\,iw):"
    "if(gt(ih\\,iw*16/9)\\,iw*16/9\\,ih),"
    "scale=1080:1920:flags=lanczos"
)

def get_duration(path: str) -> float:
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise RuntimeError(f"ffprobe не прочитал видео: {r.stderr[:200]}")

MIN_CLIP_DURATION = 15   # секунд — клипы короче этого не отправляем

def cut_and_crop(inp: str, work_dir: Path, s: dict) -> list:
    duration = get_duration(inp)
    clips, part, start = [], 1, 0.0
    while start < duration:
        chunk = random.randint(CLIP_MIN_SEC, CLIP_MAX_SEC)
        dur = min(chunk, duration - start)

        # Пропускаем последний кусок если он короче 15 секунд
        if dur < MIN_CLIP_DURATION:
            print(f"[ffmpeg] Последний кусок {dur:.1f}с < {MIN_CLIP_DURATION}с, пропускаю")
            break

        print(f"[ffmpeg] клип {part}: {dur}с (цель {chunk}с, диапазон {CLIP_MIN_SEC}–{CLIP_MAX_SEC})")
        out    = str(work_dir / f"part_{part:03d}.mp4")
        wm     = s.get("watermark_path")
        has_wm = wm and Path(wm).exists()
        cmd    = _wm_cmd(inp, out, start, dur, s) if has_wm else _plain_cmd(inp, out, start, dur)
        res    = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if res.returncode != 0 and has_wm:
            res = subprocess.run(_plain_cmd(inp, out, start, dur), capture_output=True, text=True, timeout=300)
        if res.returncode == 0 and Path(out).exists():
            clips.append(out)
        start += dur
        part  += 1
    return clips

def _plain_cmd(inp, out, start, dur):
    return [FFMPEG, "-y", "-ss", str(start), "-i", inp, "-t", str(dur),
            "-vf", CROP_SCALE, "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out]

def _wm_cmd(inp, out, start, dur, s):
    wm   = s["watermark_path"]
    size = _clamp_wm_pct(s.get("watermark_size", 20))
    _, ox, oy = POSITIONS[s.get("watermark_pos", "br")]
    w    = int(1080 * size / 100)
    if s.get("wm_type") == "gif":
        esc = wm.replace("\\", "/").replace(":", "\\:")
        fc  = (f"[0:v]{CROP_SCALE}[b];movie=filename='{esc}':loop=0,"
               f"scale={w}:-1[wm];[b][wm]overlay={ox}:{oy}[out]")
        return [FFMPEG, "-y", "-ss", str(start), "-i", inp, "-t", str(dur),
                "-filter_complex", fc, "-map", "[out]", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out]
    else:
        fc = f"[0:v]{CROP_SCALE}[b];[1:v]scale={w}:-1[wm];[b][wm]overlay={ox}:{oy}[out]"
        return [FFMPEG, "-y", "-ss", str(start), "-i", inp, "-i", wm, "-t", str(dur),
                "-filter_complex", fc, "-map", "[out]", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out]

def get_title(url: str) -> str:
    try:
        r = subprocess.run(
            ["yt-dlp", "--get-title", "--no-playlist", url],
            capture_output=True, text=True, timeout=30,
        )
        t = r.stdout.strip()
        return t if t else "Видео"
    except Exception:
        return "Видео"

def make_title(base: str, part: int, total: int, max_len: int) -> str:
    prefix = f"Часть {part}/{total} — "
    avail  = max_len - len(prefix)
    t = base if len(base) <= avail else base[:avail-1].rstrip() + "…"
    return prefix + t

def diagnose(stderr: str) -> str:
    s = stderr.lower()
    if "video unavailable" in s or "not available" in s: return "Видео недоступно"
    if "private" in s:                                   return "Приватное видео"
    if "copyright" in s or "content id" in s:           return "Авторские права"
    if "429" in s or "too many requests" in s:           return "Слишком много запросов"
    if "geo" in s or "country" in s:                    return "Гео-блокировка"
    last = "\n".join(stderr.strip().splitlines()[-2:])
    return f"Ошибка: {last[:200]}"

async def send_safe(bot, chat_id: int, path: str, caption: str) -> bool:
    for attempt in range(1, SEND_RETRIES + 1):
        try:
            with open(path, "rb") as f:
                await asyncio.wait_for(
                    bot.send_video(
                        chat_id=chat_id, video=f, caption=caption,
                        supports_streaming=True,
                        read_timeout=SEND_TIMEOUT, write_timeout=SEND_TIMEOUT,
                        connect_timeout=30,
                    ),
                    timeout=SEND_TIMEOUT + 10,
                )
            return True
        except RetryAfter as e:
            await asyncio.sleep(int(e.retry_after) + 5)
        except (TimedOut, asyncio.TimeoutError):
            if attempt == SEND_RETRIES:
                return False
            await asyncio.sleep(10 * attempt)
        except (NetworkError, Exception) as e:
            print(f"[send] попытка {attempt}: {e}")
            if attempt == SEND_RETRIES:
                return False
            await asyncio.sleep(15)
    return False


# ═══════════════════════════════════════════════════════
#  ЗАПУСК
# ═══════════════════════════════════════════════════════

async def main():
    global job_semaphore
    job_semaphore = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

    if BOT_TOKEN == "ВСТАВЬ_ТОКЕН_СЮДА":
        print("Укажи BOT_TOKEN в файле .env!")
        return
    if subprocess.run([FFMPEG, "-version"], capture_output=True).returncode != 0:
        print("ffmpeg не найден! Скачай с ffmpeg.org")
        return

    print("Бот запущен!")
    print(f"YouTube: {yt_status()}")
    if yt_ok() and not yt_auth():
        print(f"  → {yt_cookie_diagnosis()}")
    print(f"TikTok:  {tt_status()}")
    print("Ctrl+C для остановки\n")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CommandHandler("reset",    cmd_reset))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.PHOTO,        handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_dispatch))

    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )
        try:
            await asyncio.Event().wait()
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
