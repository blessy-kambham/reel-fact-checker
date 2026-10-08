"""Content Extractor agent.

Turns a short video into text the rest of the pipeline can check: speech is transcribed locally with
Whisper, on-screen text is read from keyframes with the model's image input, and an optional caption
is added. The result is one structured content object, whichever of those parts the video has.

After that first pass the agent decides whether the on-screen text needs a closer look. It sees what
was found (how much speech, what text the frames showed) and chooses a tool: `read_more_frames` to
read a denser set of frames, or `finish`. Text-heavy videos with little speech are where the extra
frames matter.

Used by: `services/video.py`.
"""
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from schemas import ContentAction, MediaSummary, ScreenText
from tools import media
from services.budget import BudgetExceeded
from tools.providers import ProviderFailure
from tools.transcribe import LOW_CONFIDENCE, Transcript

from agents.runtime import Done, PlanningUnavailable, autonomous, run_tools
from agents.shared import loose

SECTION_SPOKEN, SECTION_SCREEN, SECTION_CAPTION = 'What is said:', 'Text on screen:', 'Caption:'
EXTRA_KEYFRAMES = 8   # frames in the closer look; they fall between the first ones
READ_INSTRUCTIONS = (
    'Copy all text visible in these video frames exactly, in reading order: captions, overlays, labels, '
    'numbers. Include each distinct piece of text once even if it repeats across frames. Do not describe '
    'images, add context, translate, correct or judge truth. Return empty text when none is visible.')
TOOLS = {
    'read_more_frames': f'Read {EXTRA_KEYFRAMES} more frames, taken between the ones already read, for on-screen text.',
    'finish': 'The content found so far is enough.',
}
REVIEW_INSTRUCTIONS = (
    'You are the content extractor for a short social media video. Its speech has been transcribed and on-screen '
    'text has been read from a few evenly spaced frames. Decide whether the on-screen text needs a closer look. '
    'Choose exactly ONE tool from "tools". Choose read_more_frames when text on screen probably carries statements '
    'the first frames may have missed: for example when there is little or no speech, when the on-screen text looks '
    'cut off or reads like part of a longer sequence, or when the video is long for the number of frames read. '
    'Choose finish when the speech carries the content or the frames showed no text. Do not judge whether anything is true.')


def compose(transcript: str, screen_text: str, caption: str) -> str:
    """The normalized content object as plain text; claims must be copied from it word for word."""
    parts = [(SECTION_SPOKEN, transcript), (SECTION_SCREEN, screen_text), (SECTION_CAPTION, caption)]
    return '\n\n'.join(f'{label}\n{value.strip()}' for label, value in parts if value and value.strip())


def merge_screen_text(first: str, more: str) -> str:
    """Add lines from a second reading that the first did not already contain."""
    lines = [line.strip() for line in first.splitlines() if line.strip()]
    seen = {loose(line) for line in lines}
    for line in (line.strip() for line in more.splitlines()):
        if line and loose(line) not in seen:
            seen.add(loose(line))
            lines.append(line)
    return '\n'.join(lines)


@dataclass
class ExtractedContent:
    text: str               # transcript, on-screen text and caption as one document
    summary: MediaSummary   # what was found, so recognition mistakes are visible in the report
    frames_read: int
    steps: list = field(default_factory=list)   # what the agent chose to do beyond its first pass


class ContentExtractorAgent:
    name = 'Content Extractor'

    def __init__(self, provider, transcriber):
        self.provider, self.transcriber = provider, transcriber

    async def extract(self, path: Path, caption: str = '') -> ExtractedContent:
        info = await media.probe(path)
        steps = []
        with tempfile.TemporaryDirectory(prefix='reel-video-') as work:
            workdir = Path(work)
            transcript = (await self.transcriber.transcribe(await media.extract_audio(path, workdir))
                          if info.has_audio else Transcript(text='', language=None))
            frames = [frame.read_bytes() for frame in await media.extract_keyframes(path, workdir, info.duration)]
            screen = await self._read(frames) if frames else ''
            frames_read = len(frames)
            if frames and autonomous(self.provider, 'content_extractor'):
                # The agent's own decision: is a closer look at the on-screen text needed?
                async def read_more_frames(action):
                    nonlocal screen, frames_read
                    closer = workdir / 'closer'
                    closer.mkdir()
                    try:
                        extra = [frame.read_bytes() for frame in
                                 await media.extract_keyframes(path, closer, info.duration, EXTRA_KEYFRAMES)]
                        merged = merge_screen_text(screen, await self._read(extra)) if extra else screen
                    except BudgetExceeded:
                        raise
                    except (ProviderFailure, media.MediaRejected, media.MediaToolMissing):
                        return Done()  # The first reading stands.
                    steps.append(f'Read {len(extra)} more frames for on-screen text and found '
                                 f'{len(merged.splitlines()) - len(screen.splitlines())} more line(s).')
                    screen, frames_read = merged, frames_read + len(extra)
                    return Done()

                async def finish(action):
                    steps.append(f'Kept the first {frames_read} frames: {" ".join(action.reason.split())[:300]}')
                    return Done()

                state = {'duration_seconds': round(info.duration, 1), 'frames_read': frames_read,
                         'speech_chars': len(transcript.text), 'speech_opening': transcript.text[:400],
                         'screen_text': screen[:1500], 'caption': caption[:300], 'tools': TOOLS}
                try:
                    await run_tools(self.provider, ContentAction, REVIEW_INSTRUCTIONS, lambda steps_left, last_step: state,
                                    {'read_more_frames': read_more_frames, 'finish': finish}, max_steps=1)
                except PlanningUnavailable:
                    pass  # The first pass stands.
        confidence = getattr(transcript, 'confidence', None)
        summary = MediaSummary(duration_seconds=round(info.duration, 2), had_audio=info.has_audio,
                               transcript_language=transcript.language, frames_read=frames_read,
                               transcript_chars=len(transcript.text), screen_text_chars=len(screen), caption_chars=len(caption),
                               transcript_confidence=confidence, poor_audio=confidence is not None and confidence < LOW_CONFIDENCE)
        return ExtractedContent(text=compose(transcript.text, screen, caption), summary=summary, frames_read=frames_read,
                                steps=[f'{self.name}: {step}' for step in steps])

    async def _read(self, frames: list) -> str:
        return (await self.provider.read_images(ScreenText, READ_INSTRUCTIONS, frames)).text.strip()
