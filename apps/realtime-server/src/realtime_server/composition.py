from __future__ import annotations

from dataclasses import dataclass

import httpx

from model_router.application import create_dify_chatflow_router
from model_router.core.router import LLMRouter
from speech_router.core.asr_contracts import ASRProvider
from speech_router.core.router import SpeechRouter
from speech_router.core.tts_contracts import TTSProvider

from .settings import RealtimeSettings


@dataclass(frozen=True, slots=True)
class RuntimeComponents:
    model_router: LLMRouter
    speech_router: SpeechRouter


def compose_runtime(
    settings: RealtimeSettings,
    http: httpx.AsyncClient,
    asr: ASRProvider,
    tts: TTSProvider,
) -> RuntimeComponents:
    return RuntimeComponents(
        model_router=create_dify_chatflow_router(settings.dify, http),
        speech_router=SpeechRouter(asr, tts),
    )
