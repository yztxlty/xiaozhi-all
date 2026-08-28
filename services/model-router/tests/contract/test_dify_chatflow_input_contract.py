from datetime import date

import pytest
from pydantic import ValidationError

from model_router.providers.dify_chatflow.config import DifyChatflowSettings
from model_router.providers.dify_chatflow.input_mapper import map_chatflow_request


def test_settings_reject_plain_http_by_default() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        DifyChatflowSettings(
            base_url="http://agent.gwcz.online/v1",
            api_key="not-a-real-key",
        )


def test_settings_allow_explicit_development_http_without_exposing_key() -> None:
    settings = DifyChatflowSettings(
        base_url="http://agent.gwcz.online/v1",
        api_key="not-a-real-key",
        allow_insecure_http=True,
    )

    assert str(settings.base_url).rstrip("/") == "http://agent.gwcz.online/v1"
    assert settings.api_key.get_secret_value() == "not-a-real-key"
    assert "not-a-real-key" not in repr(settings)


def test_map_request_uses_stateless_chatflow_contract(llm_request) -> None:
    request = llm_request.model_copy(
        update={
            "role_profile": {
                "name": "幽光",
                "sex": "女",
                "persona": "温暖自然",
                "role_id": 12,
                "agent_id": 34,
                "favor": 80,
                "favor_level": "熟悉",
            }
        }
    )

    payload = map_chatflow_request(request, today=date(2026, 8, 28))

    assert payload["query"] == "你好"
    assert payload["user"] == "usr_1"
    assert payload["conversation_id"] == ""
    assert payload["response_mode"] == "streaming"
    assert payload["auto_generate_name"] is False
    inputs = payload["inputs"]
    assert inputs["person_name"] == "幽光"
    assert inputs["person_sex"] == "女"
    assert inputs["person_info"] == "温暖自然"
    assert inputs["character"] == "温暖自然"
    assert inputs["role_id"] == 12
    assert inputs["agent_id"] == 34
    assert inputs["favor"] == "80"
    assert inputs["favor_level"] == "熟悉"
    assert inputs["session_id"] == "s_1"
    assert inputs["today_date"] == "2026-08-28"
    assert "用户喜欢简洁回答" in inputs["memory"]
    assert "我在" in inputs["memory"]


def test_memory_is_bounded_to_8000_characters(llm_request) -> None:
    oversized = llm_request.model_copy(
        update={"long_memories": [{"content": "记" * 9000}]}
    )

    payload = map_chatflow_request(oversized, today=date(2026, 8, 28))

    assert len(payload["inputs"]["memory"]) <= 8000
