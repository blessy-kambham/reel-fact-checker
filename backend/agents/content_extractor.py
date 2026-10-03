"""Content Extractor agent.

Turns a short video into text the rest of the pipeline can check: speech is transcribed locally with
Whisper, on-screen text is read from keyframes with the model's image input, and an optional caption
is added. The result is one structured content object, whichever of those parts the video has.

Used by: `services/video.py`.
"""
import tempfile
from dataclasses import dataclass
from pathlib import Path

from schemas import MediaSummary, ScreenText
from services import media
from services.transcribe import Transcript

SECTION_SPOKEN, SECTION_SCREEN, SECTION_CAPTION = 'What is said:', 'Text on screen:', 'Caption:'


def compose(transcript: str, screen_text: str, caption: str) -> str:
    """The normalized content object as plain text; claims must be copied from it word for word."""
    parts = [(SECTION_SPOKEN, transcript), (SECTION_SCREEN, screen_text), (SECTION_CAPTION, caption)]
    return '\n\n'.join(f'{label}\n{value.strip()}' for label, value in parts if value and value.strip())


@dataclass
class ExtractedContent:
    text: str               # transcript, on-screen text and caption as one document
    summary: MediaSummary   # what was found, so recognition mistakes are visible in the report
    frames_read: int


class ContentExtractorAgent:
    name = 'Content Extractor'

    def __init__(self, provider, transcriber):
        self.provider, self.transcriber = provider, transcriber

    async def extract(self, path: Path, caption: str = '') -> ExtractedContent:
        info = await media.probe(path)
        with tempfile.TemporaryDirectory(prefix='reel-video-') as work:
            workdir = Path(work)
            transcript = (await self.transcriber.transcribe(await media.extract_audio(path, workdir))
                          if info.has_audio else Transcript(text='', language=None))
            frames = [frame.read_bytes() for frame in await media.extract_keyframes(path, workdir, info.duration)]
        screen = ''
        if frames:
            screen = (await self.provider.read_images(ScreenText,
                'Copy all text visible in these video frames exactly, in reading order: captions, overlays, labels, '
                'numbers. Include each distinct piece of text once even if it repeats across frames. Do not describe '
                'images, add context, translate, correct or judge truth. Return empty text when none is visible.',
                frames)).text.strip()
        summary = MediaSummary(duration_seconds=round(info.duration, 2), had_audio=info.has_audio,
                               transcript_language=transcript.language, frames_read=len(frames),
                               transcript_chars=len(transcript.text), screen_text_chars=len(screen), caption_chars=len(caption))
        return ExtractedContent(text=compose(transcript.text, screen, caption), summary=summary, frames_read=len(frames))
