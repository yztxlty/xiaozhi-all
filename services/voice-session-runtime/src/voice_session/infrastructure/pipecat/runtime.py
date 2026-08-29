from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence

from pipecat.frames.frames import (
    EndFrame,
    ErrorFrame,
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMTextFrame,
    TTSAudioRawFrame,
    TTSStoppedFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner

SendJSON = Callable[[dict], Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]
logger = logging.getLogger("voice_session.pipecat")


class _ClientTextProcessor(FrameProcessor):
    def __init__(self, send_json: SendJSON):
        super().__init__()
        self._send_json = send_json
        self._turn_started_at = 0.0
        self._first_llm_reported = False

    def start_turn(self, started_at: float) -> None:
        self._turn_started_at = started_at
        self._first_llm_reported = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMTextFrame):
            if not self._first_llm_reported:
                self._first_llm_reported = True
                logger.info(
                    "[latency] llm_first_delta_ms=%.1f",
                    (time.perf_counter() - self._turn_started_at) * 1000,
                )
            await self._send_json({"type": "llm.text.delta", "text": frame.text})
        await self.push_frame(frame, direction)


class _ClientOutputProcessor(FrameProcessor):
    def __init__(
        self,
        send_json: SendJSON,
        send_bytes: SendBytes,
        turn_done: asyncio.Event,
        interruption_done: asyncio.Event,
    ):
        super().__init__()
        self._send_json = send_json
        self._send_bytes = send_bytes
        self._turn_done = turn_done
        self._interruption_done = interruption_done
        self._audio_sequence = 0
        self._turn_started_at = 0.0
        self._first_audio_reported = False
        self._suppressed_interruptions = 0
        self._replacement_pending_audio = False

    def start_turn(self, started_at: float) -> None:
        self._turn_started_at = started_at
        self._first_audio_reported = False

    def suppress_next_interruption_notification(self) -> None:
        self._suppressed_interruptions += 1
        self._replacement_pending_audio = True

    def cancel_replacement(self) -> None:
        self._suppressed_interruptions = 0
        self._replacement_pending_audio = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TTSAudioRawFrame):
            self._replacement_pending_audio = False
            if not self._first_audio_reported:
                self._first_audio_reported = True
                logger.info(
                    "[latency] tts_first_audio_ms=%.1f",
                    (time.perf_counter() - self._turn_started_at) * 1000,
                )
            self._audio_sequence += 1
            await self._send_json({"type": "tts.audio", "seq": self._audio_sequence})
            await self._send_bytes(frame.audio)
        elif isinstance(frame, TTSStoppedFrame):
            if not self._replacement_pending_audio:
                await self._send_json({"type": "tts.done"})
                self._turn_done.set()
        elif isinstance(frame, ErrorFrame):
            await self._send_json({"type": "error", "code": "VOICE_PIPELINE_FAILED"})
            self._turn_done.set()
        elif isinstance(frame, InterruptionFrame):
            try:
                if self._suppressed_interruptions:
                    self._suppressed_interruptions -= 1
                else:
                    await self._send_json({"type": "tts.done"})
                    self._turn_done.set()
            finally:
                self._interruption_done.set()

        await self.push_frame(frame, direction)


class PipecatVoiceRuntime:
    """每条终端连接复用一条官方 Pipecat Pipeline。"""

    def __init__(
        self,
        processors: Sequence[FrameProcessor],
        send_json: SendJSON,
        send_bytes: SendBytes,
        *,
        system_instruction: str = (
            "你是幽光，温柔可爱的 AI 陪伴。这是实时语音对话，请直接使用简洁口语回答；"
            "第一句不超过十二个汉字并尽快使用句号，总回复通常不超过两句。"
            "不要使用表情符号，不要使用 Markdown、列表、链接或其他无法朗读的格式。"
        ),
    ) -> None:
        self.turn_done = asyncio.Event()
        self.turn_done.set()
        self._interruption_done = asyncio.Event()
        self._interruption_done.set()
        self._interrupt_lock = asyncio.Lock()
        self._system_instruction = system_instruction
        self._text_output = _ClientTextProcessor(send_json)
        self._output = _ClientOutputProcessor(
            send_json,
            send_bytes,
            self.turn_done,
            self._interruption_done,
        )
        if not processors:
            raise ValueError("PipecatVoiceRuntime 至少需要一个处理器")
        self._pipeline = Pipeline(
            [processors[0], self._text_output, *processors[1:], self._output]
        )
        self._worker = PipelineWorker(
            self._pipeline,
            cancel_on_idle_timeout=False,
            enable_rtvi=False,
            enable_turn_tracking=False,
            params=PipelineParams(audio_in_sample_rate=16000, audio_out_sample_rate=16000),
        )
        self._runner = WorkerRunner()
        self._runner_task: asyncio.Task | None = None
        self._started = asyncio.Event()

        @self._worker.event_handler("on_pipeline_started")
        async def _on_pipeline_started(_worker, _frame):
            self._started.set()

    @property
    def worker(self) -> PipelineWorker:
        return self._worker

    async def start(self) -> None:
        if self._runner_task is not None:
            return
        await self._runner.add_workers(self._worker)
        self._runner_task = asyncio.create_task(self._runner.run())
        await asyncio.wait_for(self._started.wait(), timeout=5)

    async def submit_text(self, text: str) -> None:
        if self._runner_task is None:
            raise RuntimeError("PipecatVoiceRuntime 尚未启动")
        self.turn_done.clear()
        started_at = time.perf_counter()
        self._text_output.start_turn(started_at)
        self._output.start_turn(started_at)
        context = LLMContext(
            [
                {"role": "system", "content": self._system_instruction},
                {"role": "user", "content": text},
            ]
        )
        await self._worker.queue_frame(LLMContextFrame(context=context))

    async def interrupt(self, *, notify_client: bool = True) -> None:
        if self._runner_task is not None and not self._runner_task.done():
            async with self._interrupt_lock:
                if not notify_client:
                    self._output.suppress_next_interruption_notification()
                else:
                    self._output.cancel_replacement()
                self._interruption_done.clear()
                await self._worker.queue_frame(InterruptionFrame())
                await asyncio.wait_for(self._interruption_done.wait(), timeout=3)

    async def close(self) -> None:
        task = self._runner_task
        if task is None:
            return
        if not task.done():
            await self._worker.queue_frame(EndFrame())
        await task
        self._runner_task = None
