from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .device_profile import DeviceProfile, profile_from_hello


class HandshakeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Handshake:
    transport: str
    audio_format: object
    device_profile: DeviceProfile


def parse_hello(payload: dict[str, Any]) -> Handshake:
    try:
        if payload.get("type") != "hello":
            raise HandshakeError("首条消息必须是 hello")
        profile = profile_from_hello(payload)
        return Handshake("websocket", profile.input_audio, profile)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, HandshakeError):
            raise
        raise HandshakeError(str(exc)) from exc
