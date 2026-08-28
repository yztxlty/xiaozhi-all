from __future__ import annotations

from contextlib import asynccontextmanager

import httpx

from speech_router.core.asr_contracts import ASRProvider
from speech_router.core.tts_contracts import TTSProvider

from .composition import RuntimeComponents, compose_runtime
from .settings import RealtimeSettings


@asynccontextmanager
async def runtime_lifespan(settings: RealtimeSettings, asr: ASRProvider, tts: TTSProvider):
    async with httpx.AsyncClient() as http:
        yield compose_runtime(settings, http, asr, tts)
