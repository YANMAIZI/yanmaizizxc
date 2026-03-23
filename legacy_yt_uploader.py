"""
yt_uploader.py — загрузка видео в YouTube Studio через Playwright + cookies (Netscape).
"""
from __future__ import annotations

import http.cookiejar
import re
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse

from config import (
    AUTO_HASHTAGS,
    YT_COOKIES_FILE,
    YT_PRIVACY,
    YT_TITLE_MAX,
    PW_PAGE_LOAD_MS,
    PW_VIDEO_PROCESS_MS,
    PW_FILE_CHOOSER_MS,
    PW_SELECTOR_MS,
)

DEBUG_DIR = Path("debug_screenshots")

# Домены, без которых Studio часто редиректит на Google Sign-In
_REQUIRED_HINT = (
    "Google открыл страницу входа — cookies для Playwright недействительны или устарели. "
    "Сделай новый экспорт в Chrome: зайди на https://studio.youtube.com (без редиректа на вход), "
    "сразу экспортируй в yt_cookies.txt (youtube.com + google.com в одном файле). "
    "Не выходи из аккаунта в браузере до экспорта. При необходимости обнови пароль/2FA-сессию в Chrome."
)


def _is_google_account_signin_url(url: str) -> bool:
    p = urlparse(url)
    h = (p.hostname or "").lower()
    return h == "accounts.google.com" or h.endswith(".accounts.google.com")


def _abort_if_signin_page(page, where: str) -> bool:
    """True = нужно выйти (открыт accounts.google.com sign-in)."""
    if not _is_google_account_signin_url(page.url):
        return False
    print(f"[yt] Страница входа Google ({where}) — сессия не принята.")
    print(f"[yt] URL: {page.url[:200]}…" if len(page.url) > 200 else f"[yt] URL: {page.url}")
    print(f"[yt] {_REQUIRED_HINT}")
    _screenshot(page, f"login_{where}")
    return True


def is_configured() -> bool:
    return Path(YT_COOKIES_FILE).exists()


def _load_jar() -> http.cookiejar.MozillaCookieJar:
    jar = http.cookiejar.MozillaCookieJar()
    jar.load(YT_COOKIES_FILE, ignore_discard=True, ignore_expires=True)
    return jar


def _has_youtube_and_google_cookies(jar: http.cookiejar.MozillaCookieJar) -> tuple[bool, bool]:
    has_yt = False
    has_google = False
    for c in jar:
        d = (c.domain or "").lower().lstrip(".")
        if "youtube" in d or "youtu.be" in d:
            has_yt = True
        if "google.com" in d or d == "google.com":
            has_google = True
    return has_yt, has_google


def is_logged_in() -> bool:
    """Файл есть и в нём есть cookies youtube + google (иначе Studio уйдёт на login)."""
    if not is_configured():
        return False
    try:
        jar = _load_jar()
        yt, g = _has_youtube_and_google_cookies(jar)
        return yt and g
    except Exception as e:
        print(f"[yt] is_logged_in: не удалось разобрать cookies: {e}")
        return False


def cookie_diagnosis() -> str:
    """Коротко для /status — почему YouTube «нужен вход»."""
    if not is_configured():
        return "нет файла yt_cookies.txt"
    try:
        jar = _load_jar()
        yt, g = _has_youtube_and_google_cookies(jar)
        miss = []
        if not yt:
            miss.append("нет cookies youtube.com")
        if not g:
            miss.append("нет cookies google.com (обязательно для studio.youtube.com)")
        return "; ".join(miss) if miss else "ок"
    except Exception as e:
        return f"не читается: {e}"


def get_channel_info(user_id: int = 0) -> dict | None:
    return {"title": "YouTube подключён", "subscribers": ""} if is_logged_in() else None


def _cookies_for_playwright(jar: http.cookiejar.MozillaCookieJar) -> list[dict]:
    """Netscape → Playwright. domain как в файле (точка в начале не трогаем)."""
    result: list[dict] = []
    for c in jar:
        dom = c.domain or ""
        if not any(
            x in dom.lower()
            for x in ("youtube", "youtu.be", "google.com", "googleapis.com")
        ):
            continue
        cookie: dict = {
            "name": c.name,
            "value": c.value,
            "domain": dom,
            "path": c.path or "/",
            "secure": bool(c.secure),
            "httpOnly": False,
            "sameSite": "None",
        }
        if c.expires and c.expires > 0:
            cookie["expires"] = float(c.expires)
        result.append(cookie)
    return result


def _screenshot(page, tag: str) -> str:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / f"yt_{tag}_{int(time.time())}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        print(f"[yt] screenshot: {path}")
        return str(path)
    except Exception as e:
        print(f"[yt] screenshot failed: {e}")
        return ""


def _host_is_studio(host: str) -> bool:
    h = (host or "").lower()
    return h == "studio.youtube.com" or h.endswith(".studio.youtube.com")


def _looks_like_google_login(page) -> bool:
    """
    Строгая проверка страницы входа Google.
    На studio.youtube.com в query часто есть signin/servicelogin в закодированных URL —
    это НЕ редирект на логин, пока hostname остаётся studio.
    """
    u = page.url
    ul = u.lower()
    p = urlparse(u)
    host = (p.hostname or "").lower()
    path = (p.path or "").lower()

    # Уже открыт Studio — не считаем «логин» по косвенным признакам в query
    if _host_is_studio(host):
        return "/signin" in path or "/reauth" in path

    if host == "accounts.google.com" or host.endswith(".accounts.google.com"):
        return True
    if host in ("www.google.com", "google.com") and "/signin" in path:
        return True
    if "servicelogin" in ul and not _host_is_studio(host):
        return True

    if host not in ("studio.youtube.com", "www.youtube.com", "youtube.com", "m.youtube.com"):
        try:
            if page.locator("input#identifierId").first.is_visible(timeout=500):
                return True
        except Exception:
            pass
    return False


def _wait_for_studio_ui(page, timeout_ms: int) -> bool:
    """
    Ждём появления корня UI Studio в DOM (attached — надёжнее visible в headless).
    """
    print("[yt] жду интерфейс Studio (ytcp-app / shell / create)…")
    combined = (
        "ytcp-app, ytcp-shell, ytcp-header, ytcp-navigation-drawer, tp-yt-app-drawer, "
        "#create-icon, ytcp-icon-button#create-icon, "
        "[aria-label='Создать'], [aria-label='Create'], "
        "[aria-label='Создать видео'], [aria-label='Upload videos']"
    )
    try:
        page.locator(combined).first.wait_for(state="attached", timeout=timeout_ms)
        time.sleep(0.8)
        print("[yt] элемент Studio в DOM (attached)")
        return True
    except Exception as e:
        print(f"[yt] интерфейс Studio не найден за {timeout_ms}ms: {e}")
        return False


def _dismiss_identity_check(page) -> None:
    """
    Закрывает диалог «Подтверждение личности» / «Verify your identity».
    Сначала пробует кликнуть «Далее» / «Next» автоматически.
    Если диалог не исчезает за 5 сек — выводит сообщение и ждёт до 120 сек
    пока пользователь закроет его сам (браузер видимый).
    """
    dialog_texts = [
        "Подтверждение личности",
        "Verify your identity",
        "подтвердите личность",
        "verify your identity",
    ]
    btn_texts = ["Далее", "Next", "Continue", "Продолжить", "ОК", "OK"]

    # Проверяем — есть ли диалог вообще
    has_dialog = False
    for text in dialog_texts:
        try:
            if page.get_by_text(text, exact=False).first.is_visible(timeout=3000):
                has_dialog = True
                print(f"[yt] обнаружен диалог: {text!r}")
                break
        except Exception:
            pass

    if not has_dialog:
        return

    # Пробуем кликнуть кнопку автоматически
    auto_clicked = False
    for btn in btn_texts:
        try:
            page.get_by_role("button", name=btn).first.click(timeout=3000)
            print(f"[yt] identity dialog: кликнул {btn!r} автоматически")
            auto_clicked = True
            time.sleep(1.5)
            break
        except Exception:
            pass
    if not auto_clicked:
        for btn in btn_texts:
            try:
                page.get_by_text(btn, exact=True).first.click(timeout=2000)
                print(f"[yt] identity dialog: кликнул текст {btn!r}")
                auto_clicked = True
                time.sleep(1.5)
                break
            except Exception:
                pass

    # Проверяем что диалог закрылся
    still_open = False
    for text in dialog_texts:
        try:
            if page.get_by_text(text, exact=False).first.is_visible(timeout=2000):
                still_open = True
                break
        except Exception:
            pass

    if still_open:
        print("[yt] ⚠️  Диалог 'Подтверждение личности' всё ещё открыт!")
        print("[yt] 👉  ЗАКРОЙ ЕГО ВРУЧНУЮ В БРАУЗЕРЕ (нажми 'Далее'), жду до 120 сек...")
        deadline = time.time() + 120
        while time.time() < deadline:
            time.sleep(2)
            gone = True
            for text in dialog_texts:
                try:
                    if page.get_by_text(text, exact=False).first.is_visible(timeout=1000):
                        gone = False
                        break
                except Exception:
                    pass
            if gone:
                print("[yt] ✅ Диалог закрыт, продолжаю автоматически")
                time.sleep(1.0)
                return
        print("[yt] ⚠️  Диалог не закрыт за 120 сек, пробую продолжить всё равно")
    else:
        print("[yt] identity dialog закрыт ✅")


def _dismiss_youtube_studio_overlays(page) -> None:
    """
    Убирает только tp-yt-iron-overlay-backdrop, мешающий клику по Create.
    НЕ удаляем tp-yt-paper-dialog / меню — иначе сносится выпадашка «Создать» → «Загрузить видео».
    """
    print("[yt] dismiss: iron-overlay backdrop (только блокирующий слой)")
    try:
        for _ in range(2):
            page.keyboard.press("Escape")
            time.sleep(0.08)
    except Exception as e:
        print(f"[yt] dismiss Escape: {e}")
    try:
        page.evaluate(
            """
            () => {
              document.querySelectorAll('tp-yt-iron-overlay-backdrop').forEach(e => {
                try {
                  e.removeAttribute('opened');
                  e.classList.remove('opened');
                  e.style.pointerEvents = 'none';
                  e.remove();
                } catch (x) {}
              });
            }
            """
        )
        print("[yt] dismiss: backdrop убран (JS)")
    except Exception as e:
        print(f"[yt] dismiss backdrop JS: {e}")
    time.sleep(0.25)


def _click_first(page, selectors: list[str], timeout_ms: int, *, force: bool = False) -> bool:
    for sel in selectors:
        try:
            print(f"[yt] try click: {sel!r} force={force}")
            page.locator(sel).first.click(timeout=timeout_ms, force=force)
            return True
        except Exception as e:
            print(f"[yt] skip {sel!r}: {e}")
    return False


def _click_create_or_upload_entry(page, selectors: list[str], timeout_ms: int) -> bool:
    _dismiss_youtube_studio_overlays(page)
    if _click_first(page, selectors, timeout_ms, force=False):
        return True
    print("[yt] retry Create with force=True")
    _dismiss_youtube_studio_overlays(page)
    return _click_first(page, selectors, timeout_ms, force=True)


def _type_into_contenteditable(page, loc, text: str, timeout_ms: int) -> bool:
    """
    Надёжный ввод в contenteditable (Shadow DOM, ytcp-*).
    fill() НЕ работает в ytcp-* компонентах — нужен keyboard.type().
    """
    try:
        loc.wait_for(state="visible", timeout=timeout_ms)
        loc.click(timeout=5000)
        time.sleep(0.2)
        page.keyboard.press("Control+a")
        page.keyboard.press("Delete")
        time.sleep(0.1)
        page.keyboard.type(text, delay=8)
        return True
    except Exception as e:
        print(f"[yt] _type_into_contenteditable: {e}")
        return False


def _fill_title_description(page, title: str, desc: str, timeout_ms: int) -> None:
    print("[yt] step: title")
    title_done = False
    for sel in [
        "ytcp-video-title #textbox",
        "ytcp-video-title [contenteditable='true']",
        "#textbox[aria-label*='title' i]",
        "#textbox[aria-label*='назван' i]",
        "[aria-label='Add a title']",
        "[aria-label='Добавить название']",
        "div#textbox[contenteditable='true']",
        "#textbox",
    ]:
        try:
            loc = page.locator(sel).first
            if _type_into_contenteditable(page, loc, title, timeout_ms):
                title_done = True
                print(f"[yt] title ok via {sel!r}")
                break
        except Exception as e:
            print(f"[yt] title fail {sel!r}: {e}")
    if not title_done:
        raise RuntimeError("Не удалось заполнить заголовок")

    time.sleep(0.4)
    print("[yt] step: description")
    desc_done = False
    for sel in [
        "ytcp-video-description #textbox",
        "ytcp-video-description [contenteditable='true']",
        "[aria-label*='Tell viewers' i]",
        "[aria-label*='Расскажите зрителям' i]",
        "[aria-label*='Description' i]",
    ]:
        try:
            loc = page.locator(sel).first
            if _type_into_contenteditable(page, loc, desc[:4900], timeout_ms):
                desc_done = True
                print(f"[yt] description ok via {sel!r}")
                break
        except Exception as e:
            print(f"[yt] description fail {sel!r}: {e}")
    if not desc_done:
        print("[yt] warning: description not filled")


def _click_not_for_kids(page, timeout_ms: int) -> None:
    print("[yt] step: not for kids")

    # Ждём появления секции «Аудитория» — она грузится после видео
    try:
        page.locator("ytcp-video-metadata-editor-basics").first.wait_for(
            state="visible", timeout=timeout_ms
        )
    except Exception:
        pass
    time.sleep(0.5)

    # JS с deep querySelector через Shadow DOM
    try:
        done = page.evaluate("""
            () => {
                function deepQuery(root, selector) {
                    const r = root.querySelector(selector);
                    if (r) return r;
                    for (const el of root.querySelectorAll('*')) {
                        if (el.shadowRoot) {
                            const found = deepQuery(el.shadowRoot, selector);
                            if (found) return found;
                        }
                    }
                    return null;
                }
                const el = deepQuery(document, "[name='NOT_MADE_FOR_KIDS']");
                if (el) { el.click(); return true; }
                // Ищем по тексту «Нет»/«No» внутри секции аудитории
                const radios = document.querySelectorAll(
                    'tp-yt-paper-radio-button, ytcp-radio-button'
                );
                for (const r of radios) {
                    if ((r.getAttribute('name') || '').includes('NOT_MADE')) {
                        r.click(); return true;
                    }
                }
                return false;
            }
        """)
        if done:
            print("[yt] not-for-kids via deep JS")
            return
    except Exception as e:
        print(f"[yt] not-for-kids JS: {e}")

    # Playwright pierce selector — пробивает Shadow DOM
    for sel in [
        "ytcp-video-metadata-editor-basics tp-yt-paper-radio-button",
        "tp-yt-paper-radio-group tp-yt-paper-radio-button:last-child",
    ]:
        try:
            # Кликаем последнюю радиокнопку в группе (обычно это «Нет, не для детей»)
            btns = page.locator(sel).all()
            if btns:
                # «Нет» обычно второй элемент (индекс 1)
                target = btns[1] if len(btns) > 1 else btns[0]
                target.click(timeout=5000, force=True)
                print(f"[yt] not-for-kids via last radio in {sel!r}")
                return
        except Exception as e:
            print(f"[yt] not-for-kids skip {sel!r}: {e}")

    print("[yt] not-for-kids: не удалось кликнуть, продолжаем (возможно уже выбрано)")


def _click_next(page, timeout_ms: int, label: str) -> bool:
    for sel in [
        "#next-button",
        "ytcp-button#next-button",
        "[aria-label='Next']",
        "[aria-label='Далее']",
        "button:has-text('Next')",
        "button:has-text('Далее')",
    ]:
        try:
            page.locator(sel).first.click(timeout=timeout_ms)
            print(f"[yt] {label} via {sel!r}")
            time.sleep(1.0)
            return True
        except Exception as e:
            print(f"[yt] next skip {sel!r}: {e}")
    return False


def _set_privacy(page, priv: str, timeout_ms: int) -> None:
    key = {"public": "PUBLIC", "private": "PRIVATE", "unlisted": "UNLISTED"}.get(
        priv.lower(), "PUBLIC"
    )
    print(f"[yt] step: privacy {key}")

    # Deep JS — обходит Shadow DOM рекурсивно
    try:
        done = page.evaluate(f"""
            () => {{
                function deepFind(root, name) {{
                    for (const el of root.querySelectorAll('tp-yt-paper-radio-button, ytcp-radio-button, [name]')) {{
                        if (el.getAttribute('name') === '{key}') {{ el.click(); return true; }}
                        if (el.shadowRoot) {{
                            if (deepFind(el.shadowRoot, name)) return true;
                        }}
                    }}
                    return false;
                }}
                return deepFind(document, '{key}');
            }}
        """)
        if done:
            print(f"[yt] privacy {key} via deep JS")
            return
    except Exception as e:
        print(f"[yt] privacy JS: {e}")

    for sel in [
        f"[name='{key}']",
        f"tp-yt-paper-radio-button[name='{key}']",
        f"ytcp-visibility-select [name='{key}']",
        f"ytcp-radio-button[name='{key}']",
    ]:
        try:
            page.locator(sel).first.click(timeout=3000, force=True)
            print(f"[yt] privacy via {sel!r}")
            return
        except Exception as e:
            print(f"[yt] privacy skip {sel!r}: {e}")
    print(f"[yt] privacy {key}: не нашли radio — возможно уже выбрано или страница другая")


def _click_publish_or_done(page, timeout_ms: int) -> None:
    print("[yt] step: publish/done")

    # Сначала закрываем диалог «Проверка видео ещё продолжается» если он есть
    # Кликаем «Опубликовать» внутри диалога
    try:
        for dlg_text in ("Проверка видео", "проверка", "Video checks", "still in progress"):
            try:
                if page.get_by_text(dlg_text, exact=False).first.is_visible(timeout=2000):
                    print(f"[yt] диалог проверки: {dlg_text!r} — кликаю Опубликовать")
                    for btn_name in ("Опубликовать", "Publish", "Опубликовать всё равно"):
                        try:
                            page.get_by_role("button", name=btn_name).first.click(timeout=3000)
                            print(f"[yt] диалог закрыт кнопкой {btn_name!r}")
                            time.sleep(1.0)
                            return
                        except Exception:
                            pass
            except Exception:
                pass
    except Exception as e:
        print(f"[yt] dialog check: {e}")

    # Основная кнопка публикации через JS
    try:
        done = page.evaluate("""
            () => {
                const b = document.querySelector('#done-button button, ytcp-button#done-button button');
                if (b && !b.disabled) { b.click(); return true; }
                const all = Array.from(document.querySelectorAll('button'));
                for (const btn of all) {
                    const t = (btn.textContent || '').trim().toLowerCase();
                    if (t === 'publish' || t === 'опубликовать' || t === 'done' || t === 'готово') {
                        if (!btn.disabled) { btn.click(); return true; }
                    }
                }
                return false;
            }
        """)
        if done:
            print("[yt] publish/done via JS")
            time.sleep(2.0)
            # Проверяем — не появился ли диалог подтверждения после клика
            for dlg_text in ("Проверка видео", "Video checks", "still in progress"):
                try:
                    if page.get_by_text(dlg_text, exact=False).first.is_visible(timeout=2000):
                        print(f"[yt] появился диалог после клика — публикую из диалога")
                        for btn_name in ("Опубликовать", "Publish"):
                            try:
                                page.get_by_role("button", name=btn_name).first.click(timeout=3000)
                                time.sleep(1.0)
                                return
                            except Exception:
                                pass
                except Exception:
                    pass
            return
    except Exception as e:
        print(f"[yt] publish JS: {e}")

    for sel in [
        "#done-button",
        "ytcp-button#done-button",
        "[aria-label='Publish']",
        "[aria-label='Опубликовать']",
        "button:has-text('Publish')",
        "button:has-text('Опубликовать')",
        "button:has-text('Done')",
        "button:has-text('Готово')",
    ]:
        try:
            page.locator(sel).first.click(timeout=timeout_ms, force=True)
            print(f"[yt] publish/done via {sel!r}")
            return
        except Exception as e:
            print(f"[yt] publish skip {sel!r}: {e}")


def _extract_watch_url(page) -> str | None:
    """
    Извлекает URL нового видео из диалога успеха Studio.
    Сначала ищет ссылку в диалоге публикации (самая точная),
    потом в текущем URL страницы, и только потом — в HTML.
    """
    # 1. Диалог успеха содержит ссылку на новое видео
    try:
        link_loc = page.locator(
            "ytcp-video-info a[href*='youtube.com/shorts'], "
            "ytcp-video-info a[href*='youtube.com/watch'], "
            ".ytcp-uploads-still-processing-dialog a[href*='youtube.com'], "
            "ytcp-video-share-dialog a[href*='youtube.com'], "
            "a[href*='youtube.com/watch?v='], "
            "a[href*='youtube.com/shorts/']"
        ).first
        href = link_loc.get_attribute("href", timeout=3000)
        if href and ("watch?v=" in href or "/shorts/" in href):
            # Убираем query параметры кроме v=
            m = re.search(r"(https?://(?:www\.)?youtube\.com/(?:watch\?v=|shorts/)[\w\-]{6,})", href)
            if m:
                print(f"[yt] URL из диалога успеха: {m.group(1)}")
                return m.group(1)
    except Exception as e:
        print(f"[yt] extract url from dialog: {e}")

    # 2. Текущий URL — после публикации Studio редиректит на страницу видео
    cur = page.url
    m = re.search(r"(https?://(?:www\.)?youtube\.com/(?:watch\?v=|shorts/)[\w\-]{6,})", cur)
    if m:
        print(f"[yt] URL из текущей страницы: {m.group(1)}")
        return m.group(1)

    # 3. Последний резерв — ищем в HTML, но только в специфичных атрибутах
    try:
        video_id = page.evaluate("""
            () => {
                // Ищем video ID в атрибутах data-* диалога публикации
                const el = document.querySelector(
                    '[video-id], [data-video-id], ytcp-video-info'
                );
                if (el) {
                    return el.getAttribute('video-id') ||
                           el.getAttribute('data-video-id') || null;
                }
                return null;
            }
        """)
        if video_id and len(video_id) >= 6:
            url = f"https://www.youtube.com/watch?v={video_id}"
            print(f"[yt] URL из data-video-id: {url}")
            return url
    except Exception as e:
        print(f"[yt] extract video-id JS: {e}")

    return None



def _schedule_or_publish(page, priv: str, timeout_ms: int) -> None:
    """
    Планирует публикацию через 12 минут.
    YouTube Studio использует ytcp-visibility-scheduler с ytcp-text-dropdown-trigger
    для даты и tp-yt-paper-input для времени.
    Раздел «Запланировать» уже раскрыт внизу страницы — просто заполняем поля.
    """
    import datetime

    print("[yt] step: schedule publish (+12 min)")
    now = datetime.datetime.now() + datetime.timedelta(minutes=12)

    # Studio хранит дату в модели компонента как {year, month(0-based), day}
    # Пробуем установить через JS напрямую в Polymer-модель
    scheduled_via_js = False
    try:
        result = page.evaluate(f"""
            () => {{
                // ytcp-visibility-scheduler содержит Polymer-модель с датой
                const scheduler = document.querySelector('ytcp-visibility-scheduler');
                if (!scheduler) return 'no scheduler';

                // Устанавливаем модель напрямую
                const model = {{
                    date: {{
                        year: {now.year},
                        month: {now.month - 1},
                        day: {now.day}
                    }},
                    selectedTimeOfDayValue: {now.hour * 60 + now.minute},
                    selectedTimezoneIndex: 0
                }};
                try {{
                    scheduler.set('model', model);
                    scheduler.model = model;
                    // Диспатчим change event чтобы Polymer обновил состояние
                    scheduler.dispatchEvent(new CustomEvent('model-changed', {{
                        detail: {{value: model}}, bubbles: true
                    }}));
                    return 'model set';
                }} catch(e) {{
                    return 'error: ' + e;
                }}
            }}
        """)
        print(f"[yt] scheduler JS: {result}")
        if result == 'model set':
            scheduled_via_js = True
            time.sleep(0.5)
    except Exception as e:
        print(f"[yt] scheduler JS error: {e}")

    # Заполняем поле времени — это обычный <input> внутри tp-yt-paper-input
    # Видно в DOM: ytcp-datetime-picker → ytcp-form-input-container → tp-yt-paper-input → input
    time_str = now.strftime("%H:%M")
    time_filled = False
    try:
        # Ищем input внутри ytcp-datetime-picker
        inp = page.locator("ytcp-datetime-picker tp-yt-paper-input input").first
        inp.wait_for(state="attached", timeout=5000)
        inp.click(timeout=3000)
        time.sleep(0.2)
        page.keyboard.press("Control+a")
        page.keyboard.type(time_str, delay=40)
        page.keyboard.press("Tab")
        time_filled = True
        print(f"[yt] time field filled: {time_str}")
    except Exception as e:
        print(f"[yt] time field error: {e}")

    # Дата — кликаем на ytcp-text-dropdown-trigger чтобы открыть календарь,
    # потом через JS выбираем нужный день
    date_set = False
    try:
        # Открываем дропдаун даты
        page.locator("ytcp-text-dropdown-trigger").first.click(timeout=5000)
        time.sleep(1.0)
        # Ищем нужный день в календаре
        day_str = str(now.day)
        # Календарь показывает числа как кнопки или div с текстом
        day_btns = page.locator(f"[data-day='{now.day}'], td:has-text('{day_str}'), [aria-label*='{day_str}']").all()
        for btn in day_btns:
            try:
                if btn.is_visible(timeout=1000):
                    btn.click(timeout=2000)
                    date_set = True
                    print(f"[yt] calendar day clicked: {day_str}")
                    time.sleep(0.5)
                    break
            except Exception:
                pass
        if not date_set:
            # Закрываем календарь и идём дальше
            page.keyboard.press("Escape")
            time.sleep(0.3)
    except Exception as e:
        print(f"[yt] date dropdown error: {e}")

    if not (scheduled_via_js or time_filled):
        print("[yt] schedule failed completely — publishing immediately as PUBLIC")
        _set_privacy(page, "public", timeout_ms)
        time.sleep(0.5)
        _click_publish_or_done(page, timeout_ms)
        return

    time.sleep(0.5)
    print(f"[yt] schedule set to {now.strftime('%d.%m.%Y %H:%M')}, clicking Schedule/Done")

    # Кнопка «Запланировать» / «Schedule» — это #done-button на этом шаге
    scheduled_btn_clicked = False
    for btn_name in ("Запланировать", "Schedule"):
        try:
            page.get_by_role("button", name=btn_name).first.click(timeout=5000)
            scheduled_btn_clicked = True
            print(f"[yt] schedule button clicked: {btn_name!r}")
            break
        except Exception as e:
            print(f"[yt] schedule btn skip {btn_name!r}: {e}")
    if not scheduled_btn_clicked:
        _click_publish_or_done(page, timeout_ms)


def upload_video(
    user_id: int,
    video_path: str,
    title: str,
    description: str = "",
    *,
    page_load_ms: int | None = None,
    video_process_ms: int | None = None,
    file_chooser_ms: int | None = None,
    selector_ms: int | None = None,
) -> dict | None:
    page_load_ms = page_load_ms if page_load_ms is not None else PW_PAGE_LOAD_MS
    video_process_ms = video_process_ms if video_process_ms is not None else PW_VIDEO_PROCESS_MS
    file_chooser_ms = file_chooser_ms if file_chooser_ms is not None else PW_FILE_CHOOSER_MS
    selector_ms = selector_ms if selector_ms is not None else PW_SELECTOR_MS

    if not is_configured():
        print(f"[yt] Нет файла {YT_COOKIES_FILE}")
        return None
    vp = Path(video_path)
    if not vp.is_file():
        print(f"[yt] Файл не найден: {video_path}")
        return None

    title = title[:YT_TITLE_MAX]
    desc = (description or f"{title}\n\n{AUTO_HASHTAGS}").strip()
    abs_path = str(vp.resolve())
    print(f"[yt] start upload: {title!r} ({vp.stat().st_size // 1024 // 1024} MB)")

    try:
        jar = _load_jar()
    except Exception as e:
        print(f"[yt] не удалось прочитать cookies: {e}")
        return None

    yt_ok, google_ok = _has_youtube_and_google_cookies(jar)
    if not yt_ok or not google_ok:
        print("[yt] В yt_cookies.txt не хватает доменов YouTube и/или Google.")
        print(f"[yt] {_REQUIRED_HINT}")
        return None

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[yt] pip install playwright && python -m playwright install chromium")
        return None

    cookies = _cookies_for_playwright(jar)
    print(f"[yt] cookies для контекста: {len(cookies)} шт.")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,   # VISIBLE — чтобы видеть что происходит
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--window-size=1280,900",
                "--start-maximized",
            ],
        )
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        ctx.add_cookies(cookies)
        page = ctx.new_page()

        try:
            print("[yt] step: warm-up youtube.com (подхват сессии)")
            page.goto("https://www.youtube.com", timeout=page_load_ms, wait_until="domcontentloaded")
            page.wait_for_load_state("load", timeout=min(page_load_ms, 20000))
            time.sleep(1.0)
            print(f"[yt] URL после youtube.com: {page.url}")
            if _abort_if_signin_page(page, "youtube"):
                return None

            print("[yt] step: open studio.youtube.com")
            page.goto("https://studio.youtube.com", timeout=page_load_ms, wait_until="domcontentloaded")
            page.wait_for_load_state("load", timeout=min(page_load_ms, 25000))
            time.sleep(2.0)
            print(f"[yt] URL после studio: {page.url}")
            if _abort_if_signin_page(page, "studio"):
                return None

            # ── Автоматически закрываем диалог «Подтверждение личности» ──────────
            # YouTube иногда показывает его при первом входе через Playwright
            _dismiss_identity_check(page)

            studio_timeout = min(45000, max(28000, page_load_ms + 10000))
            studio_ready = _wait_for_studio_ui(page, studio_timeout)
            if not studio_ready:
                if _abort_if_signin_page(page, "after_wait"):
                    return None
                p = urlparse(page.url)
                host = (p.hostname or "").lower()
                if _host_is_studio(host):
                    print(
                        "[yt] селекторы Studio не дождались, но hostname — studio.youtube.com; "
                        "пробуем загрузку (медленный headless / другой DOM)."
                    )
                elif _looks_like_google_login(page):
                    print("[yt] Редирект на вход Google (cookies неполные или устарели).")
                    print(f"[yt] {_REQUIRED_HINT}")
                    _screenshot(page, "login_redirect")
                    return None
                else:
                    print("[yt] предупреждение: Studio не подтверждён, пробуем дальше")

            print("[yt] step: Create → Upload videos (file chooser)")
            uploaded = False

            def _open_upload_dialog(page, selector_ms, file_chooser_ms, abs_path):
                """
                Двухэтапная стратегия:
                  1) Пробуем прямую ссылку a[test-id='upload-icon-url'] — она сразу
                     открывает диалог загрузки (без меню). Ждём кнопку «Выбрать файлы».
                  2) Если не получилось — кликаем кнопку Create и ищем пункт меню Upload.
                """
                # ── Стратегия 1: прямая ссылка на Upload ──────────────────────────
                print("[yt] upload strategy 1: direct upload-icon-url link")
                try:
                    loc = page.locator("a[test-id='upload-icon-url']").first
                    loc.wait_for(state="attached", timeout=selector_ms)
                    # Навигируем через href напрямую (надёжнее клика по <a>)
                    href = loc.get_attribute("href")
                    if href:
                        upload_url = href if href.startswith("http") else f"https://studio.youtube.com{href}"
                        print(f"[yt] goto upload url: {upload_url}")
                        page.goto(upload_url, timeout=30000, wait_until="domcontentloaded")
                        time.sleep(1.5)
                    else:
                        loc.click(timeout=selector_ms)
                        time.sleep(1.5)

                    # Ждём диалог загрузки и ищем input[type=file] или кнопку
                    time.sleep(1.5)
                    # Сначала пробуем скрытый input[type=file] — самый надёжный
                    try:
                        inp = page.locator("input[type='file']").first
                        inp.wait_for(state="attached", timeout=10000)
                        inp.set_input_files(abs_path)
                        print("[yt] strategy 1: file set via hidden input OK")
                        return True
                    except Exception as e_inp:
                        print(f"[yt] strategy 1 hidden input: {e_inp}")

                    # Fallback: file chooser через кнопку «Выбрать файлы»
                    select_btn_sels = [
                        "button[aria-label='Выбрать файлы']",
                        "button[aria-label='Select files']",
                        "ytcp-button-shape button",
                        ".ytcp-uploads-dialog button",
                    ]
                    btn_found = False
                    for sel in select_btn_sels:
                        try:
                            page.locator(sel).first.wait_for(state="visible", timeout=5000)
                            btn_found = True
                            print(f"[yt] upload dialog visible via {sel!r}")
                            break
                        except Exception:
                            pass

                    if btn_found:
                        with page.expect_file_chooser(timeout=file_chooser_ms) as fc_info:
                            for sel in select_btn_sels:
                                try:
                                    page.locator(sel).first.click(timeout=5000, force=True)
                                    print(f"[yt] clicked select-files via {sel!r}")
                                    break
                                except Exception:
                                    pass
                        fc_info.value.set_files(abs_path)
                        print("[yt] strategy 1: file set via chooser OK")
                        return True
                except Exception as e:
                    print(f"[yt] strategy 1 failed: {e}")

                # ── Стратегия 2: кнопка Create → меню → Upload videos ─────────────
                print("[yt] upload strategy 2: Create button → menu")
                create_selectors = [
                    "ytcp-icon-button#upload-icon",
                    "#upload-icon",
                    "#create-icon",
                    "ytcp-icon-button#create-icon",
                    "button[aria-label='Create']",
                    "button[aria-label='Создать']",
                    "[aria-label='Create']",
                    "[aria-label='Создать']",
                ]
                try:
                    with page.expect_file_chooser(timeout=file_chooser_ms) as fc_info:
                        _dismiss_youtube_studio_overlays(page)
                        clicked = _click_first(page, create_selectors, selector_ms, force=False) or                                   _click_first(page, create_selectors, selector_ms, force=True)
                        if not clicked:
                            raise RuntimeError("кнопка Create не найдена")
                        time.sleep(1.2)
                        menu_sels = [
                            "tp-yt-paper-item:has-text('Загрузить видео')",
                            "tp-yt-paper-item:has-text('Upload videos')",
                            "tp-yt-paper-item:has-text('Upload video')",
                            "tp-yt-paper-item:has-text('Загрузить')",
                            "tp-yt-paper-item:has-text('Upload')",
                            "[test-id='upload-beta-icon']",
                            "[aria-label='Upload videos']",
                            "[aria-label='Загрузить видео']",
                        ]
                        for sel in menu_sels:
                            try:
                                page.locator(sel).first.click(timeout=4000, force=True)
                                print(f"[yt] menu clicked {sel!r}")
                                break
                            except Exception as e:
                                print(f"[yt] menu skip {sel!r}: {e}")
                    fc_info.value.set_files(abs_path)
                    print("[yt] strategy 2: file set OK")
                    return True
                except Exception as e:
                    print(f"[yt] strategy 2 failed: {e}")
                    traceback.print_exc()

                # ── Стратегия 3: hidden input[type=file] ──────────────────────────
                print("[yt] upload strategy 3: hidden input[type=file]")
                try:
                    inp = page.locator("input[type='file']").first
                    inp.wait_for(state="attached", timeout=selector_ms)
                    inp.set_input_files(abs_path)
                    print("[yt] strategy 3: file set OK")
                    return True
                except Exception as e:
                    print(f"[yt] strategy 3 failed: {e}")

                return False

            try:
                uploaded = _open_upload_dialog(page, selector_ms, file_chooser_ms, abs_path)
            except Exception as e:
                print(f"[yt] upload dialog error: {e}")
                traceback.print_exc()
                _screenshot(page, "file_chooser_fail")
                uploaded = False

            if not uploaded:
                print("[yt] все стратегии не сработали — выход")
                _screenshot(page, "upload_all_failed")
                return None

            print("[yt] step: wait for metadata form")
            deadline = time.time() + video_process_ms / 1000.0
            while time.time() < deadline:
                try:
                    page.locator(
                        "ytcp-video-title #textbox, ytcp-video-title [contenteditable='true'], #textbox"
                    ).first.wait_for(state="visible", timeout=5000)
                    break
                except Exception:
                    time.sleep(1.5)
            else:
                print("[yt] timeout: форма метаданных не появилась")
                _screenshot(page, "no_metadata")
                return None

            _fill_title_description(page, title, desc, selector_ms)
            time.sleep(0.5)
            _click_not_for_kids(page, selector_ms)

            for i in range(3):
                if not _click_next(page, selector_ms, f"next {i+1}/3"):
                    print("[yt] warning: next button weak on step", i + 1)
                time.sleep(1.2)

            _schedule_or_publish(page, YT_PRIVACY, selector_ms)

            time.sleep(4.0)
            url = _extract_watch_url(page)
            if not url or "studio.youtube.com" in url:
                # Пробуем ещё раз через секунду
                time.sleep(2.0)
                url = _extract_watch_url(page) or "https://studio.youtube.com"

            print(f"[yt] done, url={url}")
            return {"url": url, "title": title}

        except Exception as e:
            print(f"[yt] error: {e}")
            traceback.print_exc()
            try:
                _screenshot(page, "error")
            except Exception:
                pass
            return None
        finally:
            browser.close()
