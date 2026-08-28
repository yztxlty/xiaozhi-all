from __future__ import annotations

import json
from datetime import date
from typing import Any

from model_router.core.contracts import LLMRequest


def _compact_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _profile_text(profile: dict[str, Any], key: str, default: str = "") -> str:
    value = profile.get(key, default)
    return "" if value is None else str(value)


def _profile_int(profile: dict[str, Any], key: str, default: int = 0) -> int:
    value = profile.get(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _memory_text(request: LLMRequest) -> str:
    memory = _compact_json(
        {
            "short_history": request.short_history,
            "long_memories": request.long_memories,
        }
    )
    return memory[:8000]


def map_chatflow_request(
    request: LLMRequest,
    *,
    today: date | None = None,
) -> dict[str, object]:
    profile = request.role_profile
    persona = _profile_text(
        profile,
        "persona",
        _profile_text(profile, "character"),
    )
    memory = _memory_text(request)
    current_date = today or date.today()

    inputs = {
        "person_name": _profile_text(profile, "name"),
        "person_sex": _profile_text(profile, "sex"),
        "person_info": _profile_text(profile, "profile", persona),
        "user_name": _profile_text(profile, "user_name"),
        "sex": _profile_text(profile, "user_sex"),
        "character": persona,
        "favor": _profile_text(profile, "favor"),
        "favor_level": _profile_text(profile, "favor_level"),
        "favor_description": _profile_text(profile, "favor_description"),
        "favor_skill": _profile_text(profile, "favor_skill"),
        "mettle": _profile_text(profile, "mettle"),
        "constell": _profile_text(profile, "constell"),
        "style_type": _profile_text(profile, "style_type"),
        "role_id": _profile_int(profile, "role_id"),
        "memory_status": 1 if request.short_history or request.long_memories else 0,
        "role_name": _profile_text(profile, "role_name"),
        "agent_id": _profile_int(profile, "agent_id"),
        "memory": memory,
        "is_os": _profile_int(profile, "is_os", 1),
        "is_reply_os": _profile_int(profile, "is_reply_os", 1),
        "model_id": _profile_text(profile, "model_id"),
        "session_id": request.session_id,
        "today_date": current_date.isoformat(),
        "prologue": _profile_text(profile, "prologue"),
    }
    return {
        "query": request.user_text,
        "user": request.user_id,
        "conversation_id": "",
        "response_mode": "streaming",
        "inputs": inputs,
        "auto_generate_name": False,
    }
