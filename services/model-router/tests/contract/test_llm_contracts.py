import pytest

from model_router.contracts import LLMProvider, LLMRequest, LLMTextDelta


def test_llm_delta_keeps_generation_identity() -> None:
    request = LLMRequest(
        session_id="s_1",
        turn_id="t_1",
        generation_id="g_1",
        user_id="usr_1",
        user_text="你好",
        role_profile={"name": "幽光"},
    )

    delta = LLMTextDelta.from_request(
        request,
        provider="dify-workflow-primary",
        sequence=1,
        text="你好",
    )

    assert delta.generation_id == "g_1"
    assert delta.sequence == 1
    assert delta.text == "你好"


def test_llm_request_rejects_blank_user_text() -> None:
    with pytest.raises(ValueError):
        LLMRequest(
            session_id="s_1",
            turn_id="t_1",
            generation_id="g_1",
            user_id="usr_1",
            user_text="   ",
            role_profile={"name": "幽光"},
        )


def test_llm_provider_is_an_abstract_streaming_contract() -> None:
    with pytest.raises(TypeError):
        LLMProvider()
