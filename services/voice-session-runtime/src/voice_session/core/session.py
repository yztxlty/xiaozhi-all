from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .cancellation import CancellationScope


class SessionState(StrEnum):
    CONNECTING = "connecting"
    READY = "ready"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    CLOSED = "closed"


class SessionTransitionError(RuntimeError):
    """会话状态迁移不符合状态机。"""


@dataclass(slots=True)
class Session:
    session_id: str
    state: SessionState = SessionState.CONNECTING
    cancel_scope: CancellationScope = field(default_factory=lambda: CancellationScope("session"))

    @classmethod
    def create(cls, session_id: str) -> Session:
        if not session_id.strip():
            raise ValueError("会话标识不能为空")
        return cls(session_id=session_id, cancel_scope=CancellationScope(f"session:{session_id}"))

    def _transition(self, expected: set[SessionState], target: SessionState) -> None:
        if self.state not in expected:
            raise SessionTransitionError(f"非法状态迁移：{self.state.value} → {target.value}")
        self.state = target

    def ready(self) -> None:
        self._transition({SessionState.CONNECTING}, SessionState.READY)

    def start_listening(self) -> None:
        self._transition({SessionState.READY}, SessionState.LISTENING)

    def commit_user_speech(self) -> None:
        self._transition({SessionState.LISTENING}, SessionState.THINKING)

    def start_speaking(self) -> None:
        self._transition({SessionState.THINKING}, SessionState.SPEAKING)

    def finish_speaking(self) -> None:
        self._transition({SessionState.SPEAKING}, SessionState.READY)

    def interrupt(self) -> None:
        self._transition({SessionState.THINKING, SessionState.SPEAKING}, SessionState.LISTENING)

    def close(self) -> None:
        if self.state is SessionState.CLOSED:
            return
        self.cancel_scope.cancel()
        self.state = SessionState.CLOSED
