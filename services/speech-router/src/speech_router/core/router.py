from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator

from .asr_contracts import ASRFinal, ASRPartial, ASRProvider
from .tts_contracts import TTSAudioChunk, TTSProvider


class SpeechRouter:
    def __init__(self, asr: ASRProvider, tts: TTSProvider) -> None:
        self.asr = asr
        self.tts = tts

    async def recognize(
        self,
        audio: AsyncIterable[bytes],
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[ASRPartial | ASRFinal]:
        async for event in self.asr.recognize(audio, cancel_event):
            if cancel_event.is_set():
                return
            yield event

    async def synthesize(
        self,
        text: str,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[TTSAudioChunk]:
        async for event in self.tts.synthesize(text, cancel_event):
            if cancel_event.is_set():
                return
            yield event
