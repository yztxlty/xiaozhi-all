import asyncio

import pytest
from pipecat.frames.frames import (
    EndFrame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.services.tts_service import TextAggregationMode
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from voice_session.infrastructure.pipecat.runtime import PipecatVoiceRuntime
from voice_session.infrastructure.pipecat.volcengine_tts import PipecatVolcengineTTSService


class FakeVoiceProcessor(FrameProcessor):
    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            await self.push_frame(LLMTextFrame("你好。"))
            await self.push_frame(
                TTSAudioRawFrame(audio=b"pcm", sample_rate=16000, num_channels=1)
            )
            await self.push_frame(LLMFullResponseEndFrame())
            await self.push_frame(TTSStoppedFrame(context_id="ctx"))
        else:
            await self.push_frame(frame, direction)


@pytest.mark.asyncio
async def test_persistent_pipecat_runtime_streams_text_and_audio() -> None:
    json_messages: list[dict] = []
    audio_messages: list[bytes] = []

    async def send_json(message: dict) -> None:
        json_messages.append(message)

    async def send_bytes(data: bytes) -> None:
        audio_messages.append(data)

    runtime = PipecatVoiceRuntime([FakeVoiceProcessor()], send_json, send_bytes)
    await runtime.start()
    await runtime.submit_text("测试")
    await asyncio.wait_for(runtime.turn_done.wait(), timeout=1)
    await runtime.close()

    assert {"type": "llm.text.delta", "text": "你好。"} in json_messages
    assert {"type": "tts.done"} in json_messages
    assert audio_messages == [b"pcm"]


@pytest.mark.asyncio
async def test_persistent_pipecat_runtime_streams_consecutive_turns() -> None:
    turn_count = 0
    json_messages: list[dict] = []

    class MultiTurnProcessor(FrameProcessor):
        async def process_frame(self, frame, direction: FrameDirection):
            nonlocal turn_count
            await super().process_frame(frame, direction)
            if isinstance(frame, LLMContextFrame):
                turn_count += 1
                await self.push_frame(LLMTextFrame(f"第{turn_count}轮。"))
                await self.push_frame(
                    TTSAudioRawFrame(audio=b"pcm", sample_rate=16000, num_channels=1)
                )
                await self.push_frame(TTSStoppedFrame(context_id=f"ctx-{turn_count}"))
                return
            await self.push_frame(frame, direction)

    async def send_json(message: dict) -> None:
        json_messages.append(message)

    async def discard(_message: bytes) -> None:
        pass

    runtime = PipecatVoiceRuntime([MultiTurnProcessor()], send_json, discard)
    await runtime.start()
    for index in range(3):
        await runtime.submit_text(f"测试{index}")
        await asyncio.wait_for(runtime.turn_done.wait(), timeout=1)
    await runtime.close()

    assert turn_count == 3
    assert [message["text"] for message in json_messages if message["type"] == "llm.text.delta"] == [
        "第1轮。", "第2轮。", "第3轮。"
    ]


@pytest.mark.asyncio
async def test_turn_finishes_only_after_tts_really_stops() -> None:
    class DelayedTTSStopProcessor(FrameProcessor):
        async def process_frame(self, frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if isinstance(frame, LLMContextFrame):
                await self.push_frame(LLMFullResponseStartFrame())
                await self.push_frame(LLMTextFrame("你好。"))
                await self.push_frame(
                    TTSAudioRawFrame(audio=b"pcm", sample_rate=16000, num_channels=1)
                )
                await self.push_frame(LLMFullResponseEndFrame())
                await asyncio.sleep(0.05)
                await self.push_frame(TTSStoppedFrame(context_id="ctx"))
                return
            await self.push_frame(frame, direction)

    async def discard(_message) -> None:
        pass

    runtime = PipecatVoiceRuntime([DelayedTTSStopProcessor()], discard, discard)
    await runtime.start()
    await runtime.submit_text("测试")
    await asyncio.sleep(0.01)

    assert not runtime.turn_done.is_set()

    await asyncio.wait_for(runtime.turn_done.wait(), timeout=1)
    await runtime.close()


@pytest.mark.asyncio
async def test_interruption_is_queued_through_pipecat() -> None:
    seen: list[type] = []

    class RecordingProcessor(FrameProcessor):
        async def process_frame(self, frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            seen.append(type(frame))
            await self.push_frame(frame, direction)

    async def discard(_message) -> None:
        pass

    runtime = PipecatVoiceRuntime([RecordingProcessor()], discard, discard)
    await runtime.start()
    await runtime.interrupt()
    assert InterruptionFrame in seen
    await runtime.close()

    assert EndFrame in seen


@pytest.mark.asyncio
async def test_internal_correction_interruption_does_not_finish_client_turn() -> None:
    json_messages: list[dict] = []

    class PassthroughProcessor(FrameProcessor):
        async def process_frame(self, frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)

    async def send_json(message: dict) -> None:
        json_messages.append(message)

    async def discard(_message) -> None:
        pass

    runtime = PipecatVoiceRuntime([PassthroughProcessor()], send_json, discard)
    await runtime.start()
    await runtime.interrupt(notify_client=False)
    await asyncio.sleep(0.05)
    assert {"type": "tts.done"} not in json_messages

    await runtime.interrupt()
    await asyncio.sleep(0.05)
    await runtime.close()

    assert json_messages.count({"type": "tts.done"}) == 1


@pytest.mark.asyncio
async def test_replacement_ignores_superseded_llm_end_before_new_audio() -> None:
    output_events: list[str] = []

    class DelayedReplacementProcessor(FrameProcessor):
        def __init__(self) -> None:
            super().__init__()
            self.turn = 0

        async def process_frame(self, frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if not isinstance(frame, LLMContextFrame):
                await self.push_frame(frame, direction)
                return
            self.turn += 1
            await self.push_frame(LLMFullResponseStartFrame())
            if self.turn == 1:
                async def finish_superseded_turn() -> None:
                    await asyncio.sleep(0.02)
                    await self.push_frame(LLMFullResponseEndFrame())

                asyncio.create_task(finish_superseded_turn())
                return
            await asyncio.sleep(0.05)
            await self.push_frame(
                TTSAudioRawFrame(audio=b"new", sample_rate=16000, num_channels=1)
            )
            await self.push_frame(LLMFullResponseEndFrame())
            await self.push_frame(TTSStoppedFrame(context_id="replacement"))

    async def send_json(message: dict) -> None:
        output_events.append(message["type"])

    async def send_bytes(_message: bytes) -> None:
        output_events.append("audio")

    runtime = PipecatVoiceRuntime(
        [DelayedReplacementProcessor()], send_json, send_bytes
    )
    await runtime.start()
    await runtime.submit_text("不完整临时文字")
    await asyncio.sleep(0.005)
    await runtime.interrupt(notify_client=False)
    await runtime.submit_text("最终完整文字")
    await asyncio.wait_for(runtime.turn_done.wait(), timeout=1)
    await runtime.close()

    assert output_events == ["tts.audio", "audio", "tts.done"]


@pytest.mark.asyncio
async def test_default_voice_prompt_forbids_unspoken_formatting() -> None:
    contexts = []

    class RecordingProcessor(FrameProcessor):
        async def process_frame(self, frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if isinstance(frame, LLMContextFrame):
                contexts.append(frame.context)
            await self.push_frame(frame, direction)

    async def discard(_message) -> None:
        pass

    runtime = PipecatVoiceRuntime([RecordingProcessor()], discard, discard)
    await runtime.start()
    await runtime.submit_text("你好")
    await asyncio.sleep(0)
    await runtime.close()

    system_text = contexts[0].messages[0]["content"]
    assert "不要使用表情符号" in system_text
    assert "不要使用 Markdown" in system_text


@pytest.mark.asyncio
async def test_volcengine_adapter_streams_llm_tokens_without_waiting_for_audio() -> None:
    class FakeProvider:
        def __init__(self):
            self.calls = []
            self.audio_sink = None

        async def start_session(self, context_id):
            self.calls.append(("start", context_id))

        def set_audio_sink(self, sink):
            self.audio_sink = sink

        async def send_text(self, context_id, text):
            self.calls.append(("text", context_id, text))

        async def finish_session(self, context_id):
            self.calls.append(("finish", context_id))

        async def wait_session_finished(self, context_id, finished):
            self.calls.append(("wait-finished", context_id, finished))

        async def cancel_session(self, context_id):
            self.calls.append(("cancel", context_id))

        async def close(self):
            self.calls.append(("close",))

    provider = FakeProvider()
    service = PipecatVolcengineTTSService(provider=provider)
    appended = []
    removed = []

    async def record_append(context_id, frame):
        appended.append((context_id, frame))

    async def record_remove(context_id):
        removed.append(context_id)

    async def discard_metric():
        pass

    service.append_to_audio_context = record_append
    service.remove_audio_context = record_remove
    service.stop_ttfb_metrics = discard_metric
    service.audio_context_available = lambda _context_id: True

    await service.on_turn_context_created("ctx")
    first = [frame async for frame in service.run_tts("你", "ctx")]
    second = [frame async for frame in service.run_tts("好", "ctx")]
    await provider.audio_sink(b"pcm")
    await service.on_turn_context_completed()

    assert service._text_aggregation_mode == TextAggregationMode.TOKEN
    assert first == [None]
    assert second == [None]
    assert isinstance(appended[0][1], TTSAudioRawFrame)
    assert appended[0][1].audio == b"pcm"
    assert appended[0][1].sample_rate == 16000
    assert appended[0][1].context_id == "ctx"
    assert isinstance(appended[1][1], TTSStoppedFrame)
    assert removed == ["ctx"]
    assert provider.calls == [
        ("start", "ctx"),
        ("text", "ctx", "你"),
        ("text", "ctx", "好"),
        ("finish", "ctx"),
        ("wait-finished", "ctx", None),
    ]
