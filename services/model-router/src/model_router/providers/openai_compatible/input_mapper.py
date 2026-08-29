from __future__ import annotations

from typing import Any

from model_router.core.contracts import LLMRequest

from .config import OpenAICompatibleSettings


def map_chat_completion_request(
    request: LLMRequest,
    settings: OpenAICompatibleSettings,
) -> dict[str, Any]:
    name = str(request.role_profile.get("name", "幽光")).strip() or "幽光"
    persona = str(request.role_profile.get("persona", "温柔可爱的 AI 陪伴")).strip()
    system_content = f"你是{name}，{persona}。" if persona else f"你是{name}。"
    messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]

    for item in request.short_history:
        role = item.get("role")
        content = item.get("content")
        if role in {"system", "user", "assistant"} and isinstance(content, str) and content.strip():
            messages.append({"role": role, "content": content.strip()})

    messages.append({"role": "user", "content": request.user_text})
    return {
        "model": settings.model,
        "messages": messages,
        "stream": True,
        "max_tokens": settings.max_tokens,
        "thinking": {"type": settings.thinking},
    }
