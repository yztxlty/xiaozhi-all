from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class LLMRequest(BaseModel):
    session_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    generation_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    device_id: str | None = None
    user_text: str = Field(min_length=1)
    role_profile: dict[str, Any]
    short_history: list[dict[str, Any]] = Field(default_factory=list)
    long_memories: list[dict[str, Any]] = Field(default_factory=list)
    scene: Literal["companion_chat", "knowledge_qa", "tool_task"] = "companion_chat"
    locale: str = "zh-CN"
    response_style: dict[str, Any] = Field(default_factory=dict)

    @field_validator("session_id", "turn_id", "generation_id", "user_id", "user_text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("字段不能为空")
        return stripped


class LLMEvent(BaseModel):
    session_id: str
    turn_id: str
    generation_id: str
    provider: str


class LLMStarted(LLMEvent):
    type: Literal["llm.started"] = "llm.started"


class LLMTextDelta(LLMEvent):
    type: Literal["llm.text.delta"] = "llm.text.delta"
    sequence: int = Field(ge=1)
    text: str = Field(min_length=1)

    @classmethod
    def from_request(
        cls,
        request: LLMRequest,
        provider: str,
        sequence: int,
        text: str,
    ) -> LLMTextDelta:
        return cls(
            session_id=request.session_id,
            turn_id=request.turn_id,
            generation_id=request.generation_id,
            provider=provider,
            sequence=sequence,
            text=text,
        )


class LLMCompleted(LLMEvent):
    type: Literal["llm.completed"] = "llm.completed"
    reply_text: str
    finish_reason: str = "stop"
    usage: dict[str, int | float] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMFailed(LLMEvent):
    type: Literal["llm.failed"] = "llm.failed"
    code: str
    retryable: bool
    delta_emitted: bool


class LLMCancelled(LLMEvent):
    type: Literal["llm.cancelled"] = "llm.cancelled"


LLMStreamEvent = LLMStarted | LLMTextDelta | LLMCompleted | LLMFailed | LLMCancelled


class LLMProvider(ABC):
    @abstractmethod
    async def stream(
        self,
        request: LLMRequest,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[LLMStreamEvent]:
        raise NotImplementedError
