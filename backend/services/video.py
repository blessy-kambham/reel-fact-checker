"""Video pipeline: upload → validate → audio + keyframes → transcript + on-screen text (+ caption)
→ one normalized text → the same verbatim claim selection and research as articles.

No custom models: ffmpeg, local Whisper and the configured OpenAI model's image input.
"""
import hashlib
from pathlib import Path
from uuid import uuid4

from agents.content_extractor import ContentExtractorAgent, compose  # noqa: F401 (compose re-exported)
from agents.shared import now
from schemas import Report
from services.article import select_and_research
from services.summary import overall
from tools.fetcher import fetch_text

MAX_CAPTION_CHARS = 2200  # Instagram's caption limit.


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


async def run_video_pipeline(path: Path, filename: str, caption: str, provider, transcriber, fetch=fetch_text) -> Report:
    caption = (caption or '').strip()[:MAX_CAPTION_CHARS]
    # Content Extractor agent: speech, on-screen text and caption become one document.
    extracted = await ContentExtractorAgent(provider, transcriber).extract(path, caption)
    content, frames_read = extracted.text, extracted.frames_read
    limitations = ['Video mode checks at most three central claims from the transcript, on-screen text and caption.',
                   'Speech recognition and on-screen text reading can make mistakes; compare the claims with the video.',
                   f'On-screen text is read from {frames_read} frames, so briefly shown text may be missed.']
    base = dict(id=str(uuid4()), mode='live', submitted_text=f'Video: {filename}'[:5000], created_at=now(),
                usage=provider.usage, input_type='video', source_sha256=file_sha256(path), source_text=content or None,
                media=extracted.summary)
    steps = list(extracted.steps)
    if not content:
        return Report(**base, intent='UNRELATED', note='No speech, on-screen text or caption was found, so there was nothing to check.',
                      claims=[], limitations=limitations, coverage_status='not_checked', agent_steps=steps)
    # Claim Extractor, then the research pipeline, exactly as for articles.
    extraction, results, refused, context_dropped, extractor_steps = await select_and_research(
        content, provider, fetch, kind='the transcript, on-screen text and caption of a short social media video')
    if context_dropped:
        limitations.append(f'{context_dropped} claim(s) were checked without surrounding context, because the context '
                           'supplied was not found word for word in the video.')
    return Report(**base, intent=extraction.intent, note=extraction.note, claims=results, limitations=limitations,
                  agent_steps=steps + extractor_steps, **overall(results),
                  omitted_claims=extraction.omitted_claims, coverage_status='incomplete' if refused else 'passed')
