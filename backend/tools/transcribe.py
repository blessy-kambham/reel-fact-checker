"""Speech-to-text. Local Whisper (faster-whisper) by default: free, and audio never leaves the machine.

Optional install: `pip install -r requirements-video.txt`. The model downloads once on first use
(WHISPER_MODEL, default "small", about 0.5 GB). A paid API transcriber can be added behind the same
interface later without touching the pipeline.
"""
import asyncio
import math
import os
from dataclasses import dataclass
from pathlib import Path

MAX_TRANSCRIPT_CHARS = 12000
# Below this the speech is reported as hard to make out. The design brief's threshold for a low-quality transcript.
LOW_CONFIDENCE = 0.6


class TranscriptionUnavailable(RuntimeError):
    """Transcription is not installed or failed; the message is safe to show."""


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None
    # How sure recognition was, 0 to 1: the average probability Whisper gave its own words, weighted by how long
    # each stretch of speech lasted. A rough measure, not an accuracy. None when there was no speech.
    confidence: float | None = None


class LocalWhisper:
    def __init__(self, model_size: str | None = None):
        self.model_size = model_size or os.getenv('WHISPER_MODEL', 'small')
        self._model = None

    @staticmethod
    def installed() -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def _load(self):
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TranscriptionUnavailable('Video transcription is not installed. Run: pip install -r requirements-video.txt') from exc
            # int8 on CPU keeps memory and time reasonable on a laptop.
            self._model = WhisperModel(self.model_size, device='cpu', compute_type='int8')
        return self._model

    def _transcribe(self, audio: Path) -> Transcript:
        segments, info = self._load().transcribe(str(audio), vad_filter=True)
        parts, spoken, weighted = [], 0.0, 0.0
        for segment in segments:
            parts.append(segment.text.strip())
            length = max(0.0, segment.end - segment.start)
            if math.isfinite(length) and math.isfinite(segment.avg_logprob):   # a value that is not a number says nothing
                spoken += length
                weighted += length * math.exp(segment.avg_logprob)
        text = ' '.join(parts).strip()
        confidence = round(min(1.0, weighted / spoken), 3) if spoken and text else None
        return Transcript(text=text[:MAX_TRANSCRIPT_CHARS], language=getattr(info, 'language', None), confidence=confidence)

    async def transcribe(self, audio: Path) -> Transcript:
        try:
            return await asyncio.to_thread(self._transcribe, audio)
        except TranscriptionUnavailable:
            raise
        except Exception as exc:
            raise TranscriptionUnavailable('The audio could not be transcribed.') from exc
