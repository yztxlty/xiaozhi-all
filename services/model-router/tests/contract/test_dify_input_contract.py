import json

from model_router.contracts import LLMRequest
from model_router.providers.dify_workflow.config import DifyWorkflowSettings
from model_router.providers.dify_workflow.input_mapper import map_dify_request


def build_request() -> LLMRequest:
    return LLMRequest(
        session_id="s_1",
        turn_id="t_1",
        generation_id="g_1",
        user_id="usr_1",
        user_text=" 今天有点累 ",
        role_profile={"name": "幽光", "persona": "温暖"},
        short_history=[{"role": "assistant", "content": "我在"}],
        long_memories=[{"id": "m_1", "content": "周五工作忙", "score": 0.9}],
    )


def test_dify_request_is_streaming_and_stateless() -> None:
    payload = map_dify_request(build_request())

    assert payload["response_mode"] == "streaming"
    assert payload["user"] == "usr_1"
    assert "conversation_id" not in payload
    assert payload["inputs"]["generation_id"] == "g_1"
    assert payload["inputs"]["user_text"] == "今天有点累"


def test_dify_context_uses_deterministic_compact_json() -> None:
    payload = map_dify_request(build_request())

    role_profile = payload["inputs"]["role_profile_json"]
    assert role_profile == '{"name":"幽光","persona":"温暖"}'
    assert json.loads(payload["inputs"]["short_history_json"])[0]["content"] == "我在"
    assert json.loads(payload["inputs"]["long_memories_json"])[0]["id"] == "m_1"


def test_dify_settings_hide_api_key() -> None:
    settings = DifyWorkflowSettings(
        base_url="https://dify.example.test/v1",
        api_key="app-secret-value",
    )

    assert "app-secret-value" not in repr(settings)
    assert str(settings.base_url).rstrip("/") == "https://dify.example.test/v1"
