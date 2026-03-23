"""
tt_uploader.py — устойчивый TikTok publisher.
Рекомендуемый режим: TikTok Content Posting API / Direct Post через refresh token.
Legacy cookies-режим оставлен только как аварийный резерв и не считается 24/7-надёжным.
"""
from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path
from typing import Any

import requests

from config import (
    TT_ACCESS_TOKEN,
    TT_API_BASE_URL,
    TT_CLIENT_KEY,
    TT_CLIENT_SECRET,
    TT_COOKIES_FILE,
    TT_DIRECT_POST_ENABLED,
    TT_HASHTAGS,
    TT_OPEN_ID,
    TT_OAUTH_TOKEN_FILE,
    TT_REFRESH_TOKEN,
    TT_TITLE_MAX,
)
from token_store import JsonTokenStore

TOKEN_STORE = JsonTokenStore(TT_OAUTH_TOKEN_FILE)


def _has_api_config() -> bool:
    return bool(TT_CLIENT_KEY and TT_CLIENT_SECRET and TT_OPEN_ID and (TT_REFRESH_TOKEN or TT_ACCESS_TOKEN))


def is_configured() -> bool:
    return _has_api_config() or Path(TT_COOKIES_FILE).exists()


def get_tiktok_user_info(user_id: int = 0) -> dict | None:
    if not is_configured():
        return None
    mode = 'official API' if _has_api_config() else 'legacy cookies'
    return {'display_name': f'TikTok подключён ({mode})'}


def _caption(title: str, tags: list[str] | None) -> str:
    title = title[:TT_TITLE_MAX].strip()
    raw_tags = tags or [tag.lstrip('#') for tag in TT_HASHTAGS.split() if tag.startswith('#')]
    suffix = ' '.join(f"#{str(tag).lstrip('#')}" for tag in raw_tags if str(tag).strip())
    return f'{title} {suffix}'.strip()


def _token_payload() -> dict[str, Any]:
    payload = TOKEN_STORE.get_platform('tiktok')
    if payload.get('access_token') and float(payload.get('expires_at') or 0) > time.time() + 120:
        return payload
    if TT_ACCESS_TOKEN and not TT_REFRESH_TOKEN:
        return {'access_token': TT_ACCESS_TOKEN, 'expires_at': time.time() + 1800}
    if not (_has_api_config() and TT_REFRESH_TOKEN):
        raise RuntimeError('TikTok API не настроен')

    response = requests.post(
        f"{TT_API_BASE_URL.rstrip('/')}/v2/oauth/token/",
        data={
            'client_key': TT_CLIENT_KEY,
            'client_secret': TT_CLIENT_SECRET,
            'grant_type': 'refresh_token',
            'refresh_token': TT_REFRESH_TOKEN,
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    access_token = data.get('access_token') or data.get('data', {}).get('access_token')
    expires_in = data.get('expires_in') or data.get('data', {}).get('expires_in', 3600)
    if not access_token:
        raise RuntimeError(f'Не удалось обновить токен TikTok: {data}')
    payload = {
        'access_token': access_token,
        'expires_at': time.time() + int(expires_in),
        'updated_at': int(time.time()),
        'raw': data,
    }
    TOKEN_STORE.save_platform('tiktok', payload)
    return payload


def _upload_via_api(video_path: str, title: str, tags: list[str] | None) -> dict | None:
    token = _token_payload()['access_token']
    video = Path(video_path)
    if not video.is_file():
        return None

    caption = _caption(title, tags)
    headers = {
        'Authorization': f'Bearer {token}',
    }
    init_payload = {
        'post_info': {
            'title': caption,
            'privacy_level': 'PUBLIC_TO_EVERYONE',
            'disable_duet': False,
            'disable_comment': False,
            'disable_stitch': False,
        },
        'source_info': {
            'source': 'FILE_UPLOAD',
            'file_name': video.name,
            'video_size': video.stat().st_size,
        },
    }
    init_response = requests.post(
        f"{TT_API_BASE_URL.rstrip('/')}/v2/post/publish/video/init/",
        headers={**headers, 'Content-Type': 'application/json'},
        data=json.dumps(init_payload),
        timeout=60,
    )
    init_response.raise_for_status()
    init_body = init_response.json()
    data = init_body.get('data', init_body)
    upload_url = data.get('upload_url') or data.get('upload_url_list', [None])[0]
    publish_id = data.get('publish_id') or data.get('publishId')
    if not upload_url:
        raise RuntimeError(f'TikTok init не вернул upload_url: {init_body}')

    mime = mimetypes.guess_type(video.name)[0] or 'video/mp4'
    with video.open('rb') as fh:
        upload_response = requests.put(
            upload_url,
            headers={'Content-Type': mime},
            data=fh,
            timeout=600,
        )
    upload_response.raise_for_status()

    out = {'caption': caption, 'mode': 'official_api'}
    if publish_id:
        out['publish_id'] = publish_id
        out['status_url'] = f"{TT_API_BASE_URL.rstrip('/')}/v2/post/publish/status/fetch/?publish_id={publish_id}"
    return out


def upload_to_tiktok(user_id: int, video_path: str, title: str, tags: list[str] | None = None, **_: Any) -> dict | None:
    if _has_api_config() and TT_DIRECT_POST_ENABLED:
        try:
            return _upload_via_api(video_path, title, tags)
        except Exception as exc:
            print(f'[tt] official API upload failed: {exc}')
            return None
    print('[tt] stable 24/7 mode requires TikTok API credentials; cookies fallback is intentionally not used as primary automation path.')
    return None
