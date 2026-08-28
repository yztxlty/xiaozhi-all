from __future__ import annotations

from dataclasses import dataclass, field

from .cancellation import CancellationScope


@dataclass(slots=True)
class Generation:
    generation_id: str
    cancel_scope: CancellationScope


@dataclass(slots=True)
class Turn:
    turn_id: str
    cancel_scope: CancellationScope
    _generation_number: int
    current_generation: Generation

    @classmethod
    def create(cls, turn_id: str, parent: CancellationScope | None = None) -> Turn:
        if not turn_id.strip():
            raise ValueError("轮次标识不能为空")
        scope = parent.child(f"turn:{turn_id}") if parent else CancellationScope(f"turn:{turn_id}")
        generation_scope = scope.child(f"generation:{turn_id}:1")
        generation = Generation(f"{turn_id}:1", generation_scope)
        return cls(turn_id, scope, 1, generation)

    def new_generation(self) -> Generation:
        self.current_generation.cancel_scope.cancel()
        self._generation_number += 1
        generation_id = f"{self.turn_id}:{self._generation_number}"
        scope = self.cancel_scope.child(f"generation:{generation_id}")
        self.current_generation = Generation(generation_id, scope)
        return self.current_generation

    def cancel(self) -> None:
        self.cancel_scope.cancel()


@dataclass(slots=True)
class PlaybackLedger:
    _segments: dict[int, str] = field(default_factory=dict)
    _confirmed: set[int] = field(default_factory=set)

    def enqueue(self, sequence: int, text: str) -> None:
        if sequence < 1:
            raise ValueError("播放序号必须从 1 开始")
        if sequence in self._segments:
            raise ValueError("播放序号不能重复")
        if not text:
            raise ValueError("播放文本不能为空")
        self._segments[sequence] = text

    def confirm_played(self, sequence: int) -> None:
        if sequence not in self._segments:
            raise ValueError("不能确认未知播放片段")
        self._confirmed.add(sequence)

    @property
    def heard_text(self) -> str:
        return "".join(
            self._segments[sequence]
            for sequence in sorted(self._segments)
            if sequence in self._confirmed
        )

    @property
    def pending_text(self) -> str:
        return "".join(
            self._segments[sequence]
            for sequence in sorted(self._segments)
            if sequence not in self._confirmed
        )
