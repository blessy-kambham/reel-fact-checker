"""Speech-to-text. Local Whisper (faster-whisper) by default: free, and audio never leaves the machine.

Optional install: `pip install -r requirements-video.txt`. The model downloads once on first use
(WHISPER_MODEL, default "small", about 0.5 GB). A paid API transcriber can be added behind the same
interface later without touching the pipeline.
"""
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

MAX_TRANSCRIPT_CHARS = 12000


class TranscriptionUnavailable(RuntimeError):
    """Transcription is not installed or failed; the message is safe to show."""


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None


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
        text = ' '.join(segment.text.strip() for segment in segments).strip()
        return Transcript(text=text[:MAX_TRANSCRIPT_CHARS], language=getattr(info, 'language', None))

    async def transcribe(self, audio: Path) -> Transcript:
        try:
            return await asyncio.to_thread(self._transcribe, audio)
        except TranscriptionUnavailable:
            raise
        except Exception as exc:
            raise TranscriptionUnavailable('The audio could not be transcribed.') from exc
