from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any


def _token(device_id: str, client_id: str) -> str:
    secret = os.getenv("XIAOZHI_DEVICE_TOKEN_SECRET", "development-only-secret").encode()
    value = f"{device_id}:{client_id}".encode()
    return hmac.new(secret, value, hashlib.sha256).hexdigest()


def verify_device_token(device_id: str, client_id: str, token: str) -> bool:
    return bool(device_id and client_id and token) and hmac.compare_digest(
        _token(device_id, client_id), token.removeprefix("Bearer ").strip()
    )


def build_ota_response(
    *, device_id: str, client_id: str, user_agent: str, language: str,
) -> dict[str, Any]:
    if not device_id.strip() or not client_id.strip():
        raise ValueError("设备身份不能为空")
    base_url = os.getenv("XIAOZHI_DEVICE_WS_URL", "ws://127.0.0.1:18765").rstrip("/")
    return {
        "websocket": {
            "url": f"{base_url}/xiaozhi/v1/ws",
            "token": _token(device_id, client_id),
            "version": 1,
        }
    }
