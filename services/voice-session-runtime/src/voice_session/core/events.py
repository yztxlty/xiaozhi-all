from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AudioInputChunk:
    session_id: str
    sequence: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class TranscriptFinal:
    session_id: str
    turn_id: str
    text: str


@dataclass(frozen=True, slots=True)
class TextDelta:
    session_id: str
    turn_id: str
    generation_id: str
    sequence: int
    text: str


@dataclass(frozen=True, slots=True)
class AudioOutputChunk:
    session_id: str
    turn_id: str
    generation_id: str
    sequence: int
    payload: bytes
