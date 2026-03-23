from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class JsonTokenStore:
    def __init__(self, path: str):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text('utf-8'))
        except Exception:
            return {}

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + '.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), 'utf-8')
        tmp.replace(self.path)

    def get_platform(self, platform: str) -> dict[str, Any]:
        return self.load().get(platform, {})

    def save_platform(self, platform: str, payload: dict[str, Any]) -> None:
        data = self.load()
        data[platform] = payload
        self.save(data)

    def is_valid(self, platform: str, skew_sec: int = 120) -> bool:
        payload = self.get_platform(platform)
        expires_at = float(payload.get('expires_at') or 0)
        access_token = payload.get('access_token')
        return bool(access_token and expires_at > time.time() + skew_sec)
