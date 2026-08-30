from __future__ import annotations

import re
import asyncio
from typing import Any

from audio_codec import DeviceOpusOutput, DevicePcmOutput
from .device_profile import ESP32_PROFILE, DeviceProfile


class DeviceOutputAdapter:
    def __init__(self, protocol: Any, profile: DeviceProfile = ESP32_PROFILE) -> None:
        self._protocol = protocol
        self._started = False
        self._text_buffer = ""
        self._audio_lock = asyncio.Lock()
        self._output = (
            DeviceOpusOutput(self._protocol.send_audio, profile.output_audio)
            if profile.output_audio.codec == "opus"
            else DevicePcmOutput(self._protocol.send_audio, profile.output_audio)
        )

    async def json(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        if message_type in {"asr.final", "stt"} and message.get("text"):
            await self._protocol.send_stt(str(message["text"]))
        elif message_type == "assistant.emotion" and message.get("emotion"):
            await self._protocol.send_llm_emotion(str(message["emotion"]))
        elif message_type == "llm.text.delta" and message.get("text"):
            self._text_buffer += str(message["text"])
            while True:
                match = re.search(r"[。！？.!?；;\n]", self._text_buffer)
                if match is None:
                    break
                sentence = self._text_buffer[: match.end()].strip()
                self._text_buffer = self._text_buffer[match.end() :]
                if sentence:
                    await self._protocol.send_tts_sentence(sentence)
        elif message_type in {"tts.done", "tts.stop"}:
            async with self._audio_lock:
                if self._started:
                    await self._output.finish()
                    await self._protocol.send_tts_stop()
                    self._started = False
                self._text_buffer = ""
        elif message_type == "device.standby":
            await self._protocol.send_standby()

    async def pcm(self, payload: bytes) -> None:
        async with self._audio_lock:
            if not self._started:
                if self._text_buffer.strip():
                    await self._protocol.send_tts_sentence(self._text_buffer.strip())
                    self._text_buffer = ""
                await self._protocol.send_tts_start()
                self._started = True
            await self._output.write(payload)
