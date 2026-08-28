from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from audio_codec import AudioFormat, DEVICE_AUDIO
from realtime_protocol import ControlMessage, ControlType, ProtocolError


class HandshakeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Handshake:
    transport: str
    audio_format: AudioFormat


def parse_hello(payload: dict[str, Any]) -> Handshake:
    try:
        message = ControlMessage.from_dict(payload)
        if message.type is not ControlType.HELLO:
            raise HandshakeError("首条消息必须是 hello")
        if message.payload.get("transport") != "websocket":
            raise HandshakeError("只支持 websocket 传输")
        audio = message.payload.get("audio_params")
        if not isinstance(audio, dict):
            raise HandshakeError("缺少音频参数")
        actual = AudioFormat(
            codec=audio.get("format"),
            sample_rate_hz=audio.get("sample_rate", 0),
            channels=audio.get("channels", 0),
            frame_duration_ms=audio.get("frame_duration", 0),
        )
        if actual != DEVICE_AUDIO:
            raise HandshakeError("设备音频参数不兼容")
        return Handshake("websocket", actual)
    except (ProtocolError, TypeError, ValueError) as exc:
        if isinstance(exc, HandshakeError):
            raise
        raise HandshakeError(str(exc)) from exc
