from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import numpy as np
import soxr

from .formats import AudioFormat
from .opus import OpusDecoder, OpusEncoder


class DeviceOpusInput:
    def __init__(self) -> None:
        self._decoder = OpusDecoder()

    def decode(self, frame: bytes) -> bytes:
        return self._decoder.decode(frame)


class DeviceOpusOutput:
    def __init__(self, send: Callable[[bytes], object], audio_format: AudioFormat | None = None) -> None:
        audio_format = audio_format or AudioFormat("opus", 16000, 1, 60)
        if audio_format.codec != "opus" or audio_format.channels != 1:
            raise ValueError("DeviceOpusOutput 只接受单声道 Opus")
        self._audio_format = audio_format
        self._source_sample_rate = 16000
        self._send = send
        self._buffer = bytearray()
        self._sent_frames = 0
        self._next_send_at = 0.0
        self._reset_stream()

    def _reset_stream(self) -> None:
        self._resampler = soxr.ResampleStream(
            self._source_sample_rate, self._audio_format.sample_rate_hz, 1, dtype="int16"
        )
        self._encoder = OpusEncoder(
            self._audio_format.sample_rate_hz,
            self._audio_format.sample_rate_hz * self._audio_format.frame_duration_ms // 1000,
        )

    async def _pace(self) -> None:
        now = time.monotonic()
        if self._sent_frames >= 5 and self._next_send_at > now:
            await asyncio.sleep(self._next_send_at - now)
            now = time.monotonic()
        if self._sent_frames == 0 or now - self._next_send_at > 0.06:
            self._next_send_at = now
        self._next_send_at += 0.06
        self._sent_frames += 1

    async def write(self, pcm: bytes) -> None:
        if len(pcm) % 2:
            raise ValueError("PCM 数据必须按 16-bit 对齐")
        samples = np.frombuffer(pcm, dtype=np.int16)
        converted = self._resampler.resample_chunk(samples)
        self._buffer.extend(converted.tobytes())
        frame_bytes = self._audio_format.sample_rate_hz * self._audio_format.frame_duration_ms // 1000 * 2
        while len(self._buffer) >= frame_bytes:
            frame = bytes(self._buffer[:frame_bytes])
            del self._buffer[:frame_bytes]
            await self._send_frame(frame)

    async def finish(self) -> None:
        converted = self._resampler.resample_chunk(np.array([], dtype=np.int16), last=True)
        self._buffer.extend(converted.tobytes())
        frame_bytes = self._audio_format.sample_rate_hz * self._audio_format.frame_duration_ms // 1000 * 2
        while len(self._buffer) >= frame_bytes:
            frame = bytes(self._buffer[:frame_bytes])
            del self._buffer[:frame_bytes]
            await self._send_frame(frame)
        if self._buffer:
            frame = bytes(self._buffer) + b"\x00" * (frame_bytes - len(self._buffer))
            await self._send_frame(frame)
            self._buffer.clear()
        self._sent_frames = 0
        self._next_send_at = 0.0
        self._reset_stream()

    async def _send_frame(self, frame: bytes) -> None:
        await self._pace()
        result = self._send(self._encoder.encode(frame))
        if hasattr(result, "__await__"):
            await result


class DevicePcmOutput:
    def __init__(self, send: Callable[[bytes], object], audio_format: AudioFormat) -> None:
        if audio_format.codec != "pcm_s16le":
            raise ValueError("DevicePcmOutput 只接受 pcm_s16le")
        self._audio_format = audio_format
        self._send = send
        self._frame_bytes = (
            audio_format.sample_rate_hz * audio_format.channels * 2
            * audio_format.frame_duration_ms // 1000
        )
        self._buffer = bytearray()
        self._sent_frames = 0
        self._next_send_at = 0.0

    async def _pace(self) -> None:
        now = time.monotonic()
        if self._sent_frames >= 5 and self._next_send_at > now:
            await asyncio.sleep(self._next_send_at - now)
            now = time.monotonic()
        frame_duration = self._frame_bytes / (
            self._audio_format.sample_rate_hz * self._audio_format.channels * 2
        )
        if self._sent_frames == 0 or now - self._next_send_at > frame_duration:
            self._next_send_at = now
        self._next_send_at += frame_duration
        self._sent_frames += 1

    async def write(self, pcm: bytes) -> None:
        if len(pcm) % 2:
            raise ValueError("PCM 数据必须按 16-bit 对齐")
        self._buffer.extend(pcm)
        while len(self._buffer) >= self._frame_bytes:
            frame = bytes(self._buffer[:self._frame_bytes])
            del self._buffer[:self._frame_bytes]
            await self._pace()
            result = self._send(frame)
            if hasattr(result, "__await__"):
                await result

    async def finish(self) -> None:
        if self._buffer:
            await self.write(bytes(self._buffer) + b"\x00" * (self._frame_bytes - len(self._buffer)))
            self._buffer.clear()
