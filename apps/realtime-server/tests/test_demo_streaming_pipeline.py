import asyncio
import logging
from types import SimpleNamespace

import pytest
from pipecat.services.deepseek.llm import DeepSeekLLMService
from pipecat.services.qwen.llm import QwenLLMService

from realtime_server import demo
from speech_router.core.asr_contracts import ASRFinal, ASRPartial
from voice_session.infrastructure.pipecat.volcengine_tts import PipecatVolcengineTTSService


def test_extract_emotion_returns_whitelist_and_safe_fallback() -> None:
    assert demo.extract_emotion("太棒了，恭喜你！") == "happy"
    assert demo.extract_emotion("听起来你很难过，我陪着你。") in {"sad", "comforting"}
    assert demo.extract_emotion("<script>alert(1)</script>") == "neutral"


class FakeHTTPClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def test_default_log_level_does_not_expose_provider_headers(monkeypatch) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    assert demo._log_level() == logging.INFO


@pytest.mark.asyncio
async def test_committed_asr_task_survives_next_audio_stream() -> None:
    """连续轮次首帧到达时，不得取消上一轮已提交但尚未收尾的任务。"""
    registry = demo._ASRTurnTaskRegistry()
    completed = asyncio.Event()

    async def committed_pipeline() -> None:
        await completed.wait()

    task = asyncio.create_task(committed_pipeline())
    registry.start(task, asyncio.Event())
    await registry.prepare_new_stream(stream_open=False)

    assert not task.cancelled()
    assert task in registry.retired

    completed.set()
    await registry.cancel_all()


def test_builds_deepseek_provider_from_selected_configuration(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "test-secret")
    monkeypatch.setenv("OPENAI_COMPATIBLE_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("OPENAI_COMPATIBLE_THINKING", "disabled")

    provider = demo._build_llm_provider(object())

    assert type(provider).__name__ == "OpenAICompatibleProvider"
    assert provider.settings.model == "deepseek-v4-flash"
    assert provider.settings.thinking == "disabled"


def test_realtime_path_uses_official_pipecat_deepseek_service(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "test-secret")
    monkeypatch.setenv("OPENAI_COMPATIBLE_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("OPENAI_COMPATIBLE_THINKING", "disabled")

    processors = demo._build_pipecat_processors(None)

    assert isinstance(processors[0], DeepSeekLLMService)
    assert isinstance(processors[1], PipecatVolcengineTTSService)


def test_device_path_preserves_tts_contract_without_changing_h5(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "test-secret")

    h5_processors = demo._build_pipecat_processors(None)
    device_processors = demo._build_pipecat_processors(None, device_mode=True)

    assert h5_processors[1]._output_sample_rate == 16000
    assert device_processors[1]._output_sample_rate == 16000
    assert device_processors[1]._provider._params["sample_rate"] == 16000


def test_realtime_path_uses_official_pipecat_qwen_service(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "qwen")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-secret")
    monkeypatch.setenv(
        "QWEN_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    monkeypatch.setenv("QWEN_MODEL", "qwen3.7-flash")

    processors = demo._build_pipecat_processors(None)

    assert isinstance(processors[0], QwenLLMService)
    assert processors[0]._settings.model == "qwen3.7-flash"
    assert isinstance(processors[1], PipecatVolcengineTTSService)


@pytest.mark.asyncio
async def test_qwen_and_tts_connections_are_warmed_before_first_user_turn(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "qwen")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-secret")
    processors = demo._build_pipecat_processors(None)
    calls: list[tuple[object, int]] = []

    async def fake_run_inference(context, max_tokens=None, **_kwargs):
        calls.append((context, max_tokens))
        return "好"

    async def fake_tts_warmup():
        calls.append(("tts", 0))

    monkeypatch.setattr(processors[0], "run_inference", fake_run_inference)
    monkeypatch.setattr(processors[1], "warmup", fake_tts_warmup)

    await demo._warmup_pipecat_processors(processors)

    assert len(calls) == 2
    assert calls[0][1] == 1
    assert calls[1] == ("tts", 0)


@pytest.mark.asyncio
async def test_final_asr_is_submitted_before_provider_cleanup_finishes(monkeypatch) -> None:
    cleanup_release = asyncio.Event()
    submitted = asyncio.Event()

    class FakeASR:
        async def recognize(self, audio, _cancel):
            async for _chunk in audio:
                pass
            yield ASRFinal("你好")
            await cleanup_release.wait()

    class FakeRuntime:
        async def submit_text(self, text):
            assert text == "你好"
            submitted.set()

    monkeypatch.setattr(demo, "DashScopeASRProvider", FakeASR)
    audio_queue = asyncio.Queue()
    await audio_queue.put(None)

    sent_messages: list[dict] = []

    async def capture(message):
        sent_messages.append(message)

    task = asyncio.create_task(
        demo._asr_then_pipeline(
            "session",
            audio_queue,
            capture,
            FakeRuntime(),
            asyncio.Event(),
            auto_commit_on_final=True,
        )
    )
    try:
        await asyncio.wait_for(submitted.wait(), timeout=0.1)
        assert not task.done()
    finally:
        cleanup_release.set()
        await task

    assert not any(message.get("type") == "asr.empty" for message in sent_messages)


@pytest.mark.asyncio
async def test_final_asr_cancels_pending_partial_before_generation_starts(monkeypatch) -> None:

    class FakeASR:
        async def recognize(self, audio, _cancel):
            await anext(audio)
            yield ASRPartial("你好")
            async for _chunk in audio:
                pass
            yield ASRFinal("你好。")

    class FakeRuntime:
        def __init__(self):
            self.submissions = []

        async def submit_text(self, text):
            self.submissions.append(text)

        async def interrupt(self):
            raise AssertionError("最终识别到达稳定窗口内时不应启动临时生成")

    monkeypatch.setattr(demo, "DashScopeASRProvider", FakeASR)
    audio_queue = asyncio.Queue()
    await audio_queue.put(b"pcm")
    await audio_queue.put(None)
    runtime = FakeRuntime()

    async def discard(_value):
        pass

    task = asyncio.create_task(
        demo._asr_then_pipeline(
            "session",
            audio_queue,
            discard,
            runtime,
            asyncio.Event(),
        )
    )

    await asyncio.wait_for(task, timeout=1)
    assert runtime.submissions == ["你好。"]


@pytest.mark.asyncio
async def test_device_final_asr_submits_without_waiting_for_asr_stream_close(monkeypatch) -> None:
    cleanup_release = asyncio.Event()

    class FakeASR:
        async def recognize(self, _audio, _cancel):
            yield ASRFinal("你好")
            await cleanup_release.wait()

    class FakeRuntime:
        def __init__(self):
            self.submissions = []
            self.submitted = asyncio.Event()

        async def submit_text(self, text):
            self.submissions.append(text)
            self.submitted.set()

    monkeypatch.setattr(demo, "DashScopeASRProvider", FakeASR)
    runtime = FakeRuntime()
    task = asyncio.create_task(
        demo._asr_then_pipeline(
            "session", asyncio.Queue(), lambda _message: asyncio.sleep(0), runtime,
            asyncio.Event(), auto_commit_on_final=True,
        )
    )
    try:
        await asyncio.wait_for(runtime.submitted.wait(), timeout=0.1)
        assert runtime.submissions == ["你好"]
    finally:
        cleanup_release.set()
        await task


@pytest.mark.asyncio
async def test_final_asr_is_submitted_when_provider_returns_before_consuming_audio_end(monkeypatch) -> None:
    """覆盖云 ASR 先发最终结果、未消费 audio sentinel 的真实时序。"""

    class FakeASR:
        async def recognize(self, _audio, _cancel):
            yield ASRFinal("你好幽光")

    class FakeRuntime:
        def __init__(self):
            self.submissions = []

        async def submit_text(self, text):
            self.submissions.append(text)

    monkeypatch.setattr(demo, "DashScopeASRProvider", FakeASR)
    runtime = FakeRuntime()
    audio_queue = asyncio.Queue()
    await audio_queue.put(None)

    async def discard(_value):
        pass

    await demo._asr_then_pipeline("session", audio_queue, discard, runtime, asyncio.Event())

    assert runtime.submissions == ["你好幽光"]


def test_keeps_dify_as_a_switchable_provider(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "dify")
    monkeypatch.setenv("DIFY_CHATFLOW_BASE_URL", "https://dify.example.com/v1")
    monkeypatch.setenv("DIFY_CHATFLOW_API_KEY", "test-secret")

    provider = demo._build_llm_provider(object())

    assert type(provider).__name__ == "DifyChatflowProvider"


def test_dify_http_client_ignores_environment_proxy(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")

    assert demo._dify_http_client_options() == {"trust_env": False}


@pytest.mark.asyncio
async def test_llm_stream_continues_while_first_sentence_is_synthesized(monkeypatch) -> None:
    second_delta_seen = asyncio.Event()
    tts_observations: list[bool] = []

    class FakeProvider:
        def __init__(self, *_args):
            pass

        async def stream(self, _request, _cancel):
            yield SimpleNamespace(type="llm.text.delta", text="第一句。")
            second_delta_seen.set()
            yield SimpleNamespace(type="llm.text.delta", text="第二句。")

    class FakeTTS:
        async def synthesize(self, _sentence, _cancel):
            tts_observations.append(second_delta_seen.is_set())
            yield SimpleNamespace(payload=b"pcm")

    monkeypatch.setattr(demo, "_dify_settings", lambda: object())
    monkeypatch.setattr(demo, "DifyChatflowClient", lambda *_args: object())
    monkeypatch.setattr(demo, "DifyChatflowProvider", FakeProvider)
    monkeypatch.setattr(demo, "VolcengineTTSProvider", FakeTTS)
    monkeypatch.setattr(demo.httpx, "AsyncClient", lambda **_kwargs: FakeHTTPClient())

    sent_json: list[dict] = []
    sent_bytes: list[bytes] = []

    async def send_json(message: dict) -> None:
        sent_json.append(message)

    async def send_bytes(data: bytes) -> None:
        sent_bytes.append(data)

    await demo._pipeline(
        "session",
        "你好",
        send_json,
        send_bytes,
        asyncio.Event(),
    )

    assert tts_observations == [True, True]
    assert sent_bytes == [b"pcm", b"pcm"]


@pytest.mark.asyncio
async def test_pipeline_passes_shared_cancel_event_to_dify(monkeypatch) -> None:
    received_cancel: list[asyncio.Event] = []

    class FakeProvider:
        def __init__(self, *_args):
            pass

        async def stream(self, _request, cancel):
            received_cancel.append(cancel)
            if False:
                yield None

    class FakeTTS:
        async def synthesize(self, _sentence, _cancel):
            if False:
                yield None

    monkeypatch.setattr(demo, "_dify_settings", lambda: object())
    monkeypatch.setattr(demo, "DifyChatflowClient", lambda *_args: object())
    monkeypatch.setattr(demo, "DifyChatflowProvider", FakeProvider)
    monkeypatch.setattr(demo, "VolcengineTTSProvider", FakeTTS)
    monkeypatch.setattr(demo.httpx, "AsyncClient", lambda **_kwargs: FakeHTTPClient())

    cancel = asyncio.Event()

    async def discard(_value) -> None:
        pass

    await demo._pipeline("session", "你好", discard, discard, cancel)

    assert received_cancel == [cancel]
