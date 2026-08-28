from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from typing import Protocol

from .cancellation import CancellationScope
from .events import AudioInputChunk, AudioOutputChunk, TextDelta, TranscriptFinal


class ASRPort(Protocol):
    def recognize(
        self,
        audio: AsyncIterable[AudioInputChunk],
        cancel_scope: CancellationScope,
    ) -> AsyncIterator[TranscriptFinal]: ...


class LLMPort(Protocol):
    def generate(
        self,
        transcript: TranscriptFinal,
        generation_id: str,
        cancel_scope: CancellationScope,
    ) -> AsyncIterator[TextDelta]: ...


class TTSPort(Protocol):
    def synthesize(
        self,
        text: AsyncIterable[TextDelta],
        cancel_scope: CancellationScope,
    ) -> AsyncIterator[AudioOutputChunk]: ...


class AudioOutputPort(Protocol):
    async def send(self, chunk: AudioOutputChunk) -> None: ...

    async def clear(self, generation_id: str) -> None: ...
