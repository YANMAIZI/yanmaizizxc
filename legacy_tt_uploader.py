"""
tt_uploader.py — загрузка в TikTok Studio через Playwright + cookies (Netscape).
"""
from __future__ import annotations

import http.cookiejar
import re
import time
import traceback
from pathlib import Path

from config import (
    TT_COOKIES_FILE,
    TT_HASHTAGS,
    TT_PROFILE_DIR,
    TT_SESSION_LOGIN_WAIT_SEC,
    TT_TITLE_MAX,
    PW_PAGE_LOAD_MS,
    PW_VIDEO_PROCESS_MS,
    PW_FILE_CHOOSER_MS,
    PW_SELECTOR_MS,
)

DEBUG_DIR = Path("debug_screenshots")


def is_configured() -> bool:
    return Path(TT_COOKIES_FILE).exists()


def _load_jar() -> http.cookiejar.MozillaCookieJar:
    jar = http.cookiejar.MozillaCookieJar()
    jar.load(TT_COOKIES_FILE, ignore_discard=True, ignore_expires=True)
    return jar


def _cookies_for_playwright(jar: http.cookiejar.MozillaCookieJar) -> list[dict]:
    result: list[dict] = []
    for c in jar:
        dom = c.domain or ""
        if "tiktok" not in dom.lower():
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


def get_tiktok_user_info(user_id: int = 0) -> dict | None:
    return {"display_name": "TikTok подключён"} if is_configured() else None


def _screenshot(page, tag: str) -> str:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / f"tt_{tag}_{int(time.time())}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        print(f"[tt] screenshot: {path}")
        return str(path)
    except Exception as e:
        print(f"[tt] screenshot failed: {e}")
        return ""


def _build_caption(title: str, tags: list[str] | None) -> str:
    t = title[:TT_TITLE_MAX].strip()
    if tags:
        hs = " ".join(f"#{str(x).lstrip('#')}" for x in tags if str(x).strip())
    else:
        hs = " ".join(
            w if w.startswith("#") else f"#{w.lstrip('#')}"
            for w in TT_HASHTAGS.split()
            if w.strip()
        )
    return f"{t} {hs}".strip()


def _looks_logged_out(page) -> bool:
    u = page.url.lower()
    if "/login" in u or "signin" in u:
        return True
    try:
        if page.get_by_text("Log in", exact=True).first.is_visible(timeout=2000):
            return True
    except Exception:
        pass
    return False


def _wait_for_manual_login(page, timeout_sec: int) -> bool:
    print("[tt] открыта страница входа TikTok.")
    print("[tt] Войди вручную в том же окне браузера. После этого профиль сохранится для следующих запусков.")
    deadline = time.time() + max(30, timeout_sec)
    while time.time() < deadline:
        time.sleep(2.0)
        if not _looks_logged_out(page):
            print("[tt] manual login completed, продолжаю загрузку")
            return True
        try:
            if "tiktokstudio/upload" not in page.url.lower():
                page.goto("https://www.tiktok.com/tiktokstudio/upload", wait_until="domcontentloaded", timeout=30000)
                page.wait_for_load_state("load", timeout=15000)
                time.sleep(1.5)
        except Exception:
            pass
    print(f"[tt] manual login timeout after {timeout_sec}s")
    _screenshot(page, "manual_login_timeout")
    return False


def _launch_session(pw, cookies: list[dict]):
    common_args = [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
    ]
    context_kwargs = dict(
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        locale="en-US",
    )

    if TT_PROFILE_DIR:
        profile_dir = Path(TT_PROFILE_DIR)
        profile_dir.mkdir(parents=True, exist_ok=True)
        print(f"[tt] using persistent profile: {profile_dir}")
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            args=common_args,
            **context_kwargs,
        )
        try:
            if cookies:
                ctx.add_cookies(cookies)
        except Exception as e:
            print(f"[tt] add_cookies into persistent profile skipped: {e}")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        return ctx, page, True

    browser = pw.chromium.launch(headless=True, args=common_args)
    ctx = browser.new_context(**context_kwargs)
    ctx.add_cookies(cookies)
    page = ctx.new_page()
    return browser, page, False


def _dismiss_tiktok_tour(page) -> None:
    """Убирает React Joyride / онбординг, который перехватывает клики над редактором."""
    print("[tt] dismiss: joyride / overlay")
    try:
        for _ in range(4):
            page.keyboard.press("Escape")
            time.sleep(0.15)
    except Exception as e:
        print(f"[tt] dismiss Escape: {e}")

    for text in (
        "Skip",
        "Skip tour",
        "Got it",
        "OK",
        "Next",
        "Пропустить",
        "Понятно",
        "Далее",
    ):
        try:
            page.get_by_role("button", name=re.compile(re.escape(text), re.I)).first.click(
                timeout=1500
            )
            print(f"[tt] tour button: {text!r}")
            time.sleep(0.25)
        except Exception:
            pass
        try:
            page.get_by_text(text, exact=True).first.click(timeout=800)
            print(f"[tt] tour text click: {text!r}")
            time.sleep(0.2)
        except Exception:
            pass

    try:
        page.evaluate(
            """
            () => {
              const portal = document.getElementById('react-joyride-portal');
              if (portal) portal.remove();
              document.querySelectorAll('[data-test-id="overlay"]').forEach(e => e.remove());
              document.querySelectorAll('.react-joyride__overlay').forEach(e => {
                e.remove();
              });
              document.querySelectorAll('.react-joyride__tooltip').forEach(e => {
                e.closest('[id^="react-joyride"]')?.remove();
              });
            }
            """
        )
        print("[tt] dismiss: joyride portal/overlay removed (JS)")
    except Exception as e:
        print(f"[tt] dismiss JS: {e}")

    time.sleep(0.35)


def _fill_caption(page, caption: str, selector_ms: int) -> bool:
    _dismiss_tiktok_tour(page)

    selectors = (
        "div.public-DraftEditor-content",
        "[data-e2e='caption-input']",
        "div.DraftEditor-root div[contenteditable='true']",
        "div[contenteditable='true']",
    )

    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=selector_ms)
            loc.click(force=True, timeout=selector_ms)
            time.sleep(0.2)
            page.keyboard.press("Control+a")
            page.keyboard.type(caption, delay=12)
            print(f"[tt] caption ok via {sel!r} (keyboard)")
            return True
        except Exception as e:
            print(f"[tt] caption skip {sel!r}: {e}")

    _dismiss_tiktok_tour(page)
    try:
        ok = page.evaluate(
            """
            (text) => {
              const el = document.querySelector('.public-DraftEditor-content')
                || document.querySelector('div[contenteditable="true"]');
              if (!el) return false;
              el.focus();
              try {
                document.execCommand('selectAll', false, null);
                document.execCommand('insertText', false, text);
              } catch (e) {
                el.innerText = text;
              }
              return true;
            }
            """,
            caption,
        )
        if ok:
            print("[tt] caption ok via execCommand / innerText")
            return True
    except Exception as e:
        print(f"[tt] caption execCommand: {e}")

    return False


def upload_to_tiktok(
    user_id: int,
    video_path: str,
    title: str,
    tags: list[str] | None = None,
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
        print(f"[tt] Нет файла {TT_COOKIES_FILE}")
        return None
    vp = Path(video_path)
    if not vp.is_file():
        print(f"[tt] Файл не найден: {video_path}")
        return None

    caption = _build_caption(title, tags)
    abs_path = str(vp.resolve())
    print(f"[tt] start: {title!r} ({vp.stat().st_size // 1024 // 1024} MB)")
    print(f"[tt] caption len={len(caption)}")

    try:
        jar = _load_jar()
    except Exception as e:
        print(f"[tt] не удалось прочитать cookies: {e}")
        return None

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[tt] pip install playwright && python -m playwright install chromium")
        return None

    cookies = _cookies_for_playwright(jar)
    print(f"[tt] cookies: {len(cookies)} шт.")

    with sync_playwright() as pw:
        session, page, is_persistent = _launch_session(pw, cookies)

        try:
            print("[tt] step: open tiktokstudio/upload")
            page.goto(
                "https://www.tiktok.com/tiktokstudio/upload",
                timeout=page_load_ms,
                wait_until="domcontentloaded",
            )
            page.wait_for_load_state("load", timeout=min(page_load_ms, 25000))
            time.sleep(2.0)

            if _looks_logged_out(page):
                print("[tt] похоже, не залогинен")
                _screenshot(page, "logged_out")
                if not (is_persistent and _wait_for_manual_login(page, TT_SESSION_LOGIN_WAIT_SEC)):
                    print("[tt] обнови tt_cookies.txt или используй TT_PROFILE_DIR для постоянной сессии")
                    return None

            print("[tt] step: Upload button → file chooser")
            uploaded = False
            try:
                with page.expect_file_chooser(timeout=file_chooser_ms) as fc_info:
                    clicked = False
                    for label in ("Upload",):
                        try:
                            page.get_by_role("button", name=label).first.click(timeout=selector_ms)
                            clicked = True
                            print("[tt] clicked Upload (role=button)")
                            break
                        except Exception as e:
                            print(f"[tt] role Upload: {e}")
                    if not clicked:
                        for sel in (
                            "button.Button__root--type-primary:has-text('Upload')",
                            "button:has-text('Upload')",
                        ):
                            try:
                                page.locator(sel).first.click(timeout=selector_ms)
                                clicked = True
                                print(f"[tt] clicked Upload ({sel!r})")
                                break
                            except Exception as e:
                                print(f"[tt] upload skip {sel!r}: {e}")
                    if not clicked:
                        raise RuntimeError("кнопка Upload не найдена")
                fc = fc_info.value
                fc.set_files(abs_path)
                uploaded = True
                print("[tt] file set via file chooser")
            except Exception as e:
                print(f"[tt] file chooser failed: {e}")
                traceback.print_exc()
                _screenshot(page, "file_chooser_fail")

            if not uploaded:
                print("[tt] fallback: input[type=file]")
                try:
                    inp = page.locator("input[type='file']").first
                    inp.wait_for(state="attached", timeout=selector_ms)
                    inp.set_input_files(abs_path)
                    uploaded = True
                    print("[tt] file set via input")
                except Exception as e2:
                    print(f"[tt] fallback: клик Upload затем input — {e2}")
                    try:
                        with page.expect_file_chooser(timeout=file_chooser_ms) as fc_info:
                            page.locator("button.Button__root--type-primary:has-text('Upload')").first.click(
                                timeout=selector_ms
                            )
                        fc_info.value.set_files(abs_path)
                        uploaded = True
                    except Exception as e3:
                        print(f"[tt] fallback failed: {e3}")
                        _screenshot(page, "upload_fallback_fail")
                        return None

            print("[tt] step: ждём форму / caption")
            deadline = time.time() + video_process_ms / 1000.0
            form_ready = False
            while time.time() < deadline:
                for sel in (
                    "[data-e2e='caption-input']",
                    "div[contenteditable='true']",
                    "div.public-DraftEditor-content",
                    ".editor-kit-editor",
                    "textarea",
                ):
                    try:
                        page.locator(sel).first.wait_for(state="visible", timeout=4000)
                        form_ready = True
                        print(f"[tt] форма видна: {sel!r}")
                        break
                    except Exception:
                        continue
                if form_ready:
                    break
                time.sleep(1.5)

            if not form_ready:
                print("[tt] timeout: поле описания не появилось")
                _screenshot(page, "no_caption")
                return None

            print("[tt] step: fill caption")
            filled = _fill_caption(page, caption, selector_ms)

            if not filled:
                print("[tt] не удалось вставить caption")
                _screenshot(page, "caption_fail")
                return None

            time.sleep(1.5)
            _dismiss_tiktok_tour(page)
            print("[tt] step: Post / Publish")
            posted = False
            for sel in (
                "[data-e2e='post_video_button']",
                "button[data-e2e='post_video_button']",
                "button.TUXButton--primary",
                "button:has-text('Post')",
                "button:has-text('Publish')",
                "div[role='button']:has-text('Post')",
            ):
                try:
                    page.locator(sel).first.click(timeout=selector_ms)
                    posted = True
                    print(f"[tt] post click {sel!r}")
                    break
                except Exception as e:
                    print(f"[tt] post skip {sel!r}: {e}")

            if not posted:
                for name in ("Post", "Publish", "Опубликовать", "发布"):
                    try:
                        page.get_by_role("button", name=name).last.click(timeout=selector_ms)
                        posted = True
                        print(f"[tt] post by name {name!r}")
                        break
                    except Exception:
                        continue

            if not posted:
                print("[tt] кнопка публикации не найдена")
                _screenshot(page, "no_post_button")
                return None

            time.sleep(5.0)
            u = page.url
            if "/upload" not in u.lower():
                print(f"[tt] ok, url={u}")
            else:
                print("[tt] ok (ещё на upload)")

            share = re.search(r"https?://(?:www\.)?tiktok\.com/@[^/]+/video/\d+", page.content())
            out_url = share.group(0) if share else "https://www.tiktok.com"
            return {"share_url": out_url, "caption": caption}

        except Exception as e:
            print(f"[tt] error: {e}")
            traceback.print_exc()
            try:
                _screenshot(page, "error")
            except Exception:
                pass
            return None
        finally:
            session.close()
