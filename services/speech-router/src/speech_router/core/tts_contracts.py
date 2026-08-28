from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TTSAudioChunk:
    sequence: int
    payload: bytes


class TTSProvider(Protocol):
    provider_id: str

    def synthesize(
        self,
        text: str,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[TTSAudioChunk]: ...
