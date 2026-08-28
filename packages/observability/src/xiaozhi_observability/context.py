from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TraceContext:
    session_id: str
    turn_id: str | None = None
    generation_id: str | None = None

    def as_log_fields(self) -> dict[str, str]:
        fields = {"session_id": self.session_id}
        if self.turn_id:
            fields["turn_id"] = self.turn_id
        if self.generation_id:
            fields["generation_id"] = self.generation_id
        return fields
