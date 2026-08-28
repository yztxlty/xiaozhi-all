from __future__ import annotations

from voice_session.core.ports import AudioOutputPort
from voice_session.core.session import Session
from voice_session.core.turn import Turn


async def handle_interruption(
    session: Session,
    turn: Turn,
    output: AudioOutputPort,
) -> None:
    generation = turn.current_generation
    generation.cancel_scope.cancel()
    await output.clear(generation.generation_id)
    session.interrupt()
