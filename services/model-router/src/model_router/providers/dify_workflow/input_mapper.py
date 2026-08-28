import json
from typing import Any

from model_router.contracts import LLMRequest


def compact_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def map_dify_request(request: LLMRequest) -> dict[str, object]:
    inputs = {
        "session_id": request.session_id,
        "turn_id": request.turn_id,
        "generation_id": request.generation_id,
        "user_text": request.user_text,
        "role_profile_json": compact_json(request.role_profile),
        "short_history_json": compact_json(request.short_history),
        "long_memories_json": compact_json(request.long_memories),
        "scene": request.scene,
        "locale": request.locale,
        "response_style_json": compact_json(request.response_style),
    }
    return {
        "inputs": inputs,
        "response_mode": "streaming",
        "user": request.user_id,
    }
