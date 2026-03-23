"""
yt_uploader.py — стабильная загрузка YouTube.
Приоритет: официальный YouTube Data API (OAuth refresh token) → legacy cookies/Playwright fallback.
"""
from __future__ import annotations

import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Any

import requests

from legacy_yt_uploader import (
    cookie_diagnosis as legacy_cookie_diagnosis,
    get_channel_info as legacy_get_channel_info,
    is_configured as legacy_is_configured,
    is_logged_in as legacy_is_logged_in,
    upload_video as legacy_upload_video,
)
from config import (
    AUTO_HASHTAGS,
    YT_CLIENT_ID,
    YT_CLIENT_SECRET,
    YT_COOKIES_FILE,
    YT_OAUTH_TOKEN_FILE,
    YT_PRIVACY,
    YT_REFRESH_TOKEN,
    YT_TITLE_MAX,
)
from token_store import JsonTokenStore

TOKEN_STORE = JsonTokenStore(YT_OAUTH_TOKEN_FILE)
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
YOUTUBE_UPLOAD_INIT_URL = 'https://www.googleapis.com/upload/youtube/v3/videos?part=snippet,status&uploadType=resumable'
YOUTUBE_VIDEO_URL = 'https://www.youtube.com/watch?v={video_id}'


def _has_official_oauth() -> bool:
    return bool(YT_CLIENT_ID and YT_CLIENT_SECRET and YT_REFRESH_TOKEN)


def is_configured() -> bool:
    return _has_official_oauth() or legacy_is_configured()


def is_logged_in() -> bool:
    if _has_official_oauth():
        return True
    return legacy_is_logged_in()


def cookie_diagnosis() -> str:
    if _has_official_oauth():
        return 'OAuth refresh token configured'
    return legacy_cookie_diagnosis()


def get_channel_info(user_id: int = 0) -> dict | None:
    if not is_logged_in():
        return None
    if _has_official_oauth():
        return {'title': 'YouTube подключён (official API)', 'subscribers': ''}
    return legacy_get_channel_info(user_id)


def _token_payload() -> dict[str, Any]:
    payload = TOKEN_STORE.get_platform('youtube')
    if payload.get('access_token') and float(payload.get('expires_at') or 0) > time.time() + 120:
        return payload

    if not _has_official_oauth():
        raise RuntimeError('YouTube OAuth не настроен')

    response = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            'client_id': YT_CLIENT_ID,
            'client_secret': YT_CLIENT_SECRET,
            'refresh_token': YT_REFRESH_TOKEN,
            'grant_type': 'refresh_token',
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    payload = {
        'access_token': data['access_token'],
        'expires_at': time.time() + int(data.get('expires_in', 3600)),
        'token_type': data.get('token_type', 'Bearer'),
        'scope': data.get('scope', ''),
        'updated_at': int(time.time()),
    }
    TOKEN_STORE.save_platform('youtube', payload)
    return payload


def _upload_via_official_api(video_path: str, title: str, description: str) -> dict | None:
    video = Path(video_path)
    if not video.is_file():
        return None

    token = _token_payload()
    mime_type = mimetypes.guess_type(video.name)[0] or 'video/mp4'
    metadata = {
        'snippet': {
            'title': title[:YT_TITLE_MAX],
            'description': (description or f'{title}\n\n{AUTO_HASHTAGS}').strip(),
            'categoryId': '22',
        },
        'status': {
            'privacyStatus': YT_PRIVACY,
            'selfDeclaredMadeForKids': False,
        },
    }
    init_headers = {
        'Authorization': f"Bearer {token['access_token']}",
        'Content-Type': 'application/json; charset=UTF-8',
        'X-Upload-Content-Length': str(video.stat().st_size),
        'X-Upload-Content-Type': mime_type,
    }
    init_response = requests.post(
        YOUTUBE_UPLOAD_INIT_URL,
        headers=init_headers,
        data=json.dumps(metadata),
        timeout=60,
    )
    init_response.raise_for_status()
    upload_url = init_response.headers.get('Location')
    if not upload_url:
        raise RuntimeError('YouTube API не вернул upload URL')

    with video.open('rb') as fh:
        upload_response = requests.put(
            upload_url,
            headers={
                'Authorization': f"Bearer {token['access_token']}",
                'Content-Type': mime_type,
            },
            data=fh,
            timeout=600,
        )
    upload_response.raise_for_status()
    body = upload_response.json()
    video_id = body.get('id')
    if not video_id:
        raise RuntimeError('YouTube API не вернул video id')
    return {'url': YOUTUBE_VIDEO_URL.format(video_id=video_id), 'title': title[:YT_TITLE_MAX], 'mode': 'official_api'}


def _upload_via_legacy_cookies(video_path: str, title: str, description: str) -> dict | None:
    print('[yt] official API unavailable, falling back to legacy cookies uploader')
    return legacy_upload_video(0, video_path, title, description)


def upload_video(user_id: int, video_path: str, title: str, description: str = '', **_: Any) -> dict | None:
    if _has_official_oauth():
        try:
            return _upload_via_official_api(video_path, title, description)
        except Exception as exc:
            print(f'[yt] official API upload failed: {exc}')
    return _upload_via_legacy_cookies(video_path, title, description)
