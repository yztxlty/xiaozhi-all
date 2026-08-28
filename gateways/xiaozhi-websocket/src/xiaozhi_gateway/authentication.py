from __future__ import annotations

from typing import Protocol


class DeviceAuthenticator(Protocol):
    async def authenticate(self, device_id: str, token: str) -> bool: ...
