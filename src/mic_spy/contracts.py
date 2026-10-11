"""Shared contracts: mono signed little-endian int16 PCM and sample intervals."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Transcript:
    start_sample: int
    end_sample: int
    text: str
    reason: str


class AudioSource(Protocol):
    sample_rate: int
    def read(self) -> bytes: ...
    def close(self) -> None: ...


class SpeechRecognizer(Protocol):
    def accept(self, pcm: bytes) -> list[Transcript]: ...
    def finish(self, reason: str) -> list[Transcript]: ...


class RecordingStore(Protocol):
    def maintenance(self) -> None: ...
    def anchor_time(self) -> None: ...
    def append(self, pcm: bytes) -> None: ...
    def transcript(self, start: int, end: int, text: str, reason: str) -> None: ...
    def close(self, status: str = 'stopped') -> None: ...
