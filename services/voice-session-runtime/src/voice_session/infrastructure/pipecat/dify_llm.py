from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from typing import Any

from model_router.core.contracts import LLMFailed, LLMRequest, LLMTextDelta
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class DifyPipecatLLM(FrameProcessor):
    """将现有 Dify Provider 的增量事件转换为 Pipecat 文本帧。"""

    def __init__(self, provider: Any, *, fallback_text: str | None = None) -> None:
        super().__init__()
        self._provider = provider
        self._fallback_text = fallback_text
        self._stream_task: asyncio.Task | None = None
        self._cancel_event: asyncio.Event | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if not isinstance(frame, LLMContextFrame):
            if isinstance(frame, InterruptionFrame):
                if self._cancel_event is not None:
                    self._cancel_event.set()
                if self._stream_task is not None:
                    self._stream_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self._stream_task
                    self._stream_task = None
            await self.push_frame(frame, direction)
            return
        messages = frame.context.get_messages()
        user_text = next(
            (str(item.get("content", "")) for item in reversed(messages) if item.get("role") == "user"),
            "",
        ).strip()
        if not user_text:
            return
        request = LLMRequest(
            session_id=f"pipecat-{uuid.uuid4().hex}",
            turn_id=f"turn-{uuid.uuid4().hex}",
            generation_id=f"generation-{uuid.uuid4().hex}",
            user_id="device",
            user_text=user_text,
            role_profile={"name": "幽光", "persona": "温柔可爱的 AI 陪伴"},
        )
        if self._stream_task is not None:
            self._stream_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._stream_task
        self._cancel_event = asyncio.Event()
        self._stream_task = asyncio.create_task(
            self._stream(request, direction, self._cancel_event)
        )

    async def _stream(
        self, request: LLMRequest, direction: FrameDirection, cancel_event: asyncio.Event
    ) -> None:
        try:
            await self.push_frame(LLMFullResponseStartFrame(), direction)
            async for event in self._provider.stream(request, cancel_event):
                if isinstance(event, LLMTextDelta):
                    await self.push_frame(LLMTextFrame(event.text), direction)
                elif isinstance(event, LLMFailed) and self._fallback_text:
                    await self.push_frame(LLMTextFrame(self._fallback_text), direction)
            await self.push_frame(LLMFullResponseEndFrame(), direction)
        except asyncio.CancelledError:
            cancel_event.set()
            raise
