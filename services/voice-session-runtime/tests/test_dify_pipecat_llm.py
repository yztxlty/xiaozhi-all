from model_router.core.contracts import LLMCompleted, LLMTextDelta
from voice_session.infrastructure.pipecat.dify_llm import DifyPipecatLLM
from pipecat.frames.frames import InterruptionFrame, LLMContextFrame, LLMFullResponseEndFrame, LLMTextFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection
import asyncio


class Provider:
    async def stream(self, request, cancel):
        yield LLMTextDelta.from_request(request, "dify", 1, "你好")
        yield LLMCompleted(
            session_id=request.session_id, turn_id=request.turn_id,
            generation_id=request.generation_id, provider="dify", reply_text="你好",
        )


async def test_dify_adapter_emits_pipecat_text_frames():
    processor = DifyPipecatLLM(Provider())
    output = []
    async def collect(frame, _direction):
        output.append(frame)
    processor.push_frame = collect
    await processor.process_frame(
        LLMContextFrame(context=LLMContext([{"role": "user", "content": "你好"}])) ,
        FrameDirection.DOWNSTREAM,
    )
    await asyncio.sleep(0)
    assert any(isinstance(frame, LLMTextFrame) and frame.text == "你好" for frame in output)
    assert any(isinstance(frame, LLMFullResponseEndFrame) for frame in output)


async def test_dify_adapter_cancels_active_stream():
    cancelled = False

    class SlowProvider:
        async def stream(self, request, cancel):
            nonlocal cancelled
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled = True
                raise
            yield LLMTextDelta.from_request(request, "dify", 1, "不会输出")

    processor = DifyPipecatLLM(SlowProvider())
    processor.push_frame = lambda *_args: asyncio.sleep(0)
    processor._start_interruption = lambda: asyncio.sleep(0)
    await processor.process_frame(
        LLMContextFrame(context=LLMContext([{"role": "user", "content": "你好"}])),
        FrameDirection.DOWNSTREAM,
    )
    await asyncio.sleep(0)
    await processor.process_frame(InterruptionFrame(), FrameDirection.DOWNSTREAM)
    assert cancelled
