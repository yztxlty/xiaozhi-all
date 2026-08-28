import pytest

from model_router.contracts import LLMRequest
from model_router.providers.dify_chatflow.config import DifyChatflowSettings


@pytest.fixture
def chatflow_settings() -> DifyChatflowSettings:
    return DifyChatflowSettings(
        base_url="https://dify.example.test/v1",
        api_key="test-key-not-a-real-secret",
    )


@pytest.fixture
def llm_request() -> LLMRequest:
    return LLMRequest(
        session_id="s_1",
        turn_id="t_1",
        generation_id="g_1",
        user_id="usr_1",
        user_text="你好",
        role_profile={"name": "幽光", "persona": "温暖自然"},
        short_history=[{"role": "assistant", "content": "我在"}],
        long_memories=[{"id": "m_1", "content": "用户喜欢简洁回答"}],
    )
