from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ASRPartial:
    text: str


@dataclass(frozen=True, slots=True)
class ASRFinal:
    text: str


class ASRProvider(Protocol):
    provider_id: str

    def recognize(
        self,
        audio: AsyncIterable[bytes],
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[ASRPartial | ASRFinal]: ...
