from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class AudioFormat:
    codec: Literal["opus", "pcm_s16le"]
    sample_rate_hz: int
    channels: int
    frame_duration_ms: int

    def __post_init__(self) -> None:
        if self.sample_rate_hz <= 0:
            raise ValueError("采样率必须大于零")
        if self.channels not in {1, 2}:
            raise ValueError("只支持单声道或双声道")
        if self.frame_duration_ms <= 0:
            raise ValueError("帧时长必须大于零")


DEVICE_AUDIO = AudioFormat(
    codec="opus",
    sample_rate_hz=16000,
    channels=1,
    frame_duration_ms=60,
)
