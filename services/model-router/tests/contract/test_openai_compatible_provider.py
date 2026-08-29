import asyncio
from types import SimpleNamespace

import httpx
import pytest

from model_router.core.contracts import LLMProvider, LLMRequest
from model_router.providers.openai_compatible.client import OpenAICompatibleClient
from model_router.providers.openai_compatible.config import OpenAICompatibleSettings
from model_router.providers.openai_compatible.input_mapper import map_chat_completion_request
from model_router.providers.openai_compatible.provider import OpenAICompatibleProvider


def _settings() -> OpenAICompatibleSettings:
    return OpenAICompatibleSettings(
        base_url="https://api.deepseek.com",
        api_key="test-secret",
        model="deepseek-v4-flash",
        thinking="disabled",
    )


def _request() -> LLMRequest:
    return LLMRequest(
        session_id="session-1",
        turn_id="turn-1",
        generation_id="generation-1",
        user_id="user-1",
        user_text="你好",
        role_profile={"name": "幽光", "persona": "温柔可爱的 AI 陪伴"},
        short_history=[{"role": "assistant", "content": "我在这里。"}],
    )


def test_maps_request_to_non_thinking_streaming_chat_completion() -> None:
    payload = map_chat_completion_request(_request(), _settings())

    assert payload["model"] == "deepseek-v4-flash"
    assert payload["stream"] is True
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["messages"] == [
        {"role": "system", "content": "你是幽光，温柔可爱的 AI 陪伴。"},
        {"role": "assistant", "content": "我在这里。"},
        {"role": "user", "content": "你好"},
    ]


@pytest.mark.asyncio
async def test_client_parses_ordered_sse_content_and_done() -> None:
    body = (
        'data: {"choices":[{"delta":{"content":"你好"},"finish_reason":null}]}\n\n'
        'data: {"choices":[{"delta":{"content":"呀"},"finish_reason":"stop"}],'
        '"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}}\n\n'
        "data: [DONE]\n\n"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as http:
        events = [event async for event in OpenAICompatibleClient(_settings(), http).stream({"stream": True})]

    assert [event.text for event in events if event.text] == ["你好", "呀"]
    assert events[-1].done is True
    assert events[-1].finish_reason == "stop"
    assert events[-1].usage["total_tokens"] == 5


@pytest.mark.asyncio
async def test_provider_emits_contract_events_and_honours_cancellation() -> None:
    class FakeClient:
        async def stream(self, _payload):
            yield SimpleNamespace(text="你好", done=False, finish_reason=None, usage={})
            await asyncio.sleep(60)

    cancel = asyncio.Event()
    provider = OpenAICompatibleProvider(_settings(), FakeClient())
    assert isinstance(provider, LLMProvider)
    events = []

    async for event in provider.stream(_request(), cancel):
        events.append(event)
        if event.type == "llm.text.delta":
            cancel.set()

    assert [event.type for event in events] == [
        "llm.started",
        "llm.text.delta",
        "llm.cancelled",
    ]
