from __future__ import annotations

import json
from typing import Any

from .device_profile import ESP32_PROFILE, DeviceProfile


class DeviceProtocolV1:
    def __init__(self, websocket: Any, session_id: str, profile: DeviceProfile = ESP32_PROFILE) -> None:
        self._websocket = websocket
        self._session_id = session_id
        self._profile = profile

    async def _send(self, payload: dict[str, Any]) -> None:
        await self._websocket.send(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    async def parse(self, message: str | bytes) -> dict[str, Any]:
        if isinstance(message, bytes):
            if not message:
                raise ValueError("音频帧不能为空")
            return {"type": "audio", "payload": message}
        try:
            payload = json.loads(message)
        except json.JSONDecodeError as exc:
            raise ValueError("控制消息不是合法 JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("type"), str):
            raise ValueError("控制消息必须是 JSON 对象")
        return payload

    async def send_hello(self) -> None:
        await self._send({
            "type": "hello",
            "version": 1,
            "transport": "websocket",
            "session_id": self._session_id,
            "audio_params": {
                "format": self._profile.output_audio.codec,
                "sample_rate": self._profile.output_audio.sample_rate_hz,
                "channels": self._profile.output_audio.channels,
                "frame_duration": self._profile.output_audio.frame_duration_ms,
            },
        })

    async def send_stt(self, text: str) -> None:
        await self._send({"type": "stt", "text": text, "session_id": self._session_id})

    async def send_tts_start(self) -> None:
        await self._send({"type": "tts", "state": "start", "session_id": self._session_id})

    async def send_tts_sentence(self, text: str) -> None:
        await self._send({"type": "tts", "state": "sentence_start", "text": text, "session_id": self._session_id})

    async def send_llm_emotion(self, emotion: str) -> None:
        await self._send({"type": "llm", "emotion": emotion, "session_id": self._session_id})

    async def send_audio(self, payload: bytes) -> None:
        await self._websocket.send(payload)

    async def send_tts_stop(self) -> None:
        await self._send({"type": "tts", "state": "stop", "session_id": self._session_id})

    async def send_standby(self) -> None:
        await self._send({"type": "system", "command": "standby", "session_id": self._session_id})
