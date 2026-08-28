from __future__ import annotations

from dataclasses import dataclass


class AudioFrameError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AudioFrame:
    sequence: int
    payload: bytes

    @classmethod
    def create(cls, sequence: int, payload: bytes) -> AudioFrame:
        if sequence < 1:
            raise AudioFrameError("音频帧序号必须从 1 开始")
        if not payload or len(payload) > 4096:
            raise AudioFrameError("音频帧长度不合法")
        return cls(sequence, payload)
