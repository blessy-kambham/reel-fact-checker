"""Video pipeline: upload → validate → audio + keyframes → transcript + on-screen text (+ caption)
→ one normalized text → the same verbatim claim selection and research as articles.

No custom models: ffmpeg, local Whisper and the configured OpenAI model's image input.
"""
import hashlib
import tempfile
from pathlib import Path
from uuid import uuid4

from schemas import MediaSummary, Report, ScreenText
from services import media
from services.article import select_and_research
from services.fetcher import fetch_text
from services.pipeline import now
from services.transcribe import Transcript

MAX_CAPTION_CHARS = 2200  # Instagram's caption limit.
SECTION_SPOKEN, SECTION_SCREEN, SECTION_CAPTION = 'What is said:', 'Text on screen:', 'Caption:'


def compose(transcript: str, screen_text: str, caption: str) -> str:
    """The normalized content object as plain text; claims must be copied from it word for word."""
    parts = [(SECTION_SPOKEN, transcript), (SECTION_SCREEN, screen_text), (SECTION_CAPTION, caption)]
    return '\n\n'.join(f'{label}\n{value.strip()}' for label, value in parts if value and value.strip())


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


async def run_video_pipeline(path: Path, filename: str, caption: str, provider, transcriber, fetch=fetch_text) -> Report:
    info = await media.probe(path)
    caption = (caption or '').strip()[:MAX_CAPTION_CHARS]
    with tempfile.TemporaryDirectory(prefix='reel-video-') as work:
        workdir = Path(work)
        transcript = (await transcriber.transcribe(await media.extract_audio(path, workdir))
                      if info.has_audio else Transcript(text='', language=None))
        frames = [frame.read_bytes() for frame in await media.extract_keyframes(path, workdir, info.duration)]
    screen = ''
    if frames:
        screen = (await provider.read_images(ScreenText,
            'Copy all text visible in these video frames exactly, in reading order: captions, overlays, labels, '
            'numbers. Include each distinct piece of text once even if it repeats across frames. Do not describe '
            'images, add context, translate, correct or judge truth. Return empty text when none is visible.',
            frames)).text.strip()
    content = compose(transcript.text, screen, caption)
    summary = MediaSummary(duration_seconds=round(info.duration, 2), had_audio=info.has_audio,
                           transcript_language=transcript.language, frames_read=len(frames),
                           transcript_chars=len(transcript.text), screen_text_chars=len(screen), caption_chars=len(caption))
    limitations = ['Video mode checks at most three central claims from the transcript, on-screen text and caption.',
                   'Speech recognition and on-screen text reading can make mistakes; compare the claims with the video.',
                   f'On-screen text is read from {len(frames)} frames, so briefly shown text may be missed.']
    base = dict(id=str(uuid4()), mode='live', submitted_text=f'Video: {filename}'[:5000], created_at=now(),
                usage=provider.usage, input_type='video', source_sha256=file_sha256(path), source_text=content or None,
                media=summary)
    if not content:
        return Report(**base, intent='UNRELATED', note='No speech, on-screen text or caption was found, so there was nothing to check.',
                      claims=[], limitations=limitations, coverage_status='not_checked')
    extraction, results, refused, context_dropped = await select_and_research(
        content, provider, fetch, kind='the transcript, on-screen text and caption of a short social media video')
    if context_dropped:
        limitations.append(f'{context_dropped} claim(s) were checked without surrounding context, because the context '
                           'supplied was not found word for word in the video.')
    return Report(**base, intent=extraction.intent, note=extraction.note, claims=results, limitations=limitations,
                  omitted_claims=extraction.omitted_claims, coverage_status='incomplete' if refused else 'passed')
