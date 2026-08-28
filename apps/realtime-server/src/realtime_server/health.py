from __future__ import annotations

from .composition import RuntimeComponents


def health_snapshot(components: RuntimeComponents) -> dict[str, str]:
    return {
        "status": "ready",
        "model_router": "ready" if components.model_router.providers else "failed",
        "speech_router": "ready" if components.speech_router.asr and components.speech_router.tts else "failed",
    }
