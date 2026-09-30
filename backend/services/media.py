"""Safe local media handling with ffmpeg/ffprobe: validation, audio and keyframes.

Every call is a fixed argument list (no shell), reads local files only (network protocols are
disabled), and has a time limit. Uploaded media is never sent anywhere by this module.
"""
import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

MAX_VIDEO_BYTES = 100 * 1024 * 1024
MAX_VIDEO_SECONDS = 180
ALLOWED_FORMATS = {'mp4', 'mov', 'webm', 'matroska'}
KEYFRAMES = 4
KEYFRAME_WIDTH = 768
TOOL_TIMEOUT = 120
LOCAL_ONLY = ['-protocol_whitelist', 'file']


class MediaRejected(ValueError):
    """The upload is not a video this app will process; the message is safe to show."""


class MediaToolMissing(RuntimeError):
    """ffmpeg/ffprobe is not installed."""


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    has_audio: bool
    format_name: str


def tools_available() -> bool:
    return bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))


async def _run(*args: str) -> bytes:
    if not shutil.which(args[0]):
        raise MediaToolMissing(f'{args[0]} is not installed.')
    process = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.DEVNULL,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=TOOL_TIMEOUT)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise MediaRejected('Processing the video took too long.')
    if process.returncode:
        # Tool error output can echo file contents; never surface it.
        raise MediaRejected('The file could not be read as a supported video.')
    return stdout


async def probe(path: Path) -> MediaInfo:
    if path.stat().st_size > MAX_VIDEO_BYTES:
        raise MediaRejected(f'Videos must be at most {MAX_VIDEO_BYTES // (1024 * 1024)} MB.')
    raw = await _run('ffprobe', '-v', 'error', *LOCAL_ONLY, '-show_entries', 'format=duration,format_name:stream=codec_type',
                     '-of', 'json', str(path))
    try:
        data = json.loads(raw)
        formats = set(data['format']['format_name'].split(','))
        duration = float(data['format']['duration'])
        streams = {s.get('codec_type') for s in data.get('streams', [])}
    except (ValueError, KeyError, TypeError):
        raise MediaRejected('The file could not be read as a supported video.')
    if not formats & ALLOWED_FORMATS:
        raise MediaRejected('Upload an MP4, MOV or WebM video.')
    if 'video' not in streams:
        raise MediaRejected('The file has no video track.')
    if not 0 < duration <= MAX_VIDEO_SECONDS:
        raise MediaRejected(f'Videos must be at most {MAX_VIDEO_SECONDS // 60} minutes long.')
    return MediaInfo(duration=duration, has_audio='audio' in streams, format_name=data['format']['format_name'])


async def extract_audio(path: Path, workdir: Path) -> Path:
    """16 kHz mono WAV, the input Whisper expects."""
    target = workdir / 'audio.wav'
    await _run('ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', *LOCAL_ONLY, '-i', str(path),
               '-vn', '-ac', '1', '-ar', '16000', '-t', str(MAX_VIDEO_SECONDS), '-y', str(target))
    return target


async def extract_keyframes(path: Path, workdir: Path, duration: float, count: int = KEYFRAMES) -> list[Path]:
    """Evenly spaced JPEG frames, scaled down; enough to read overlays and captions."""
    frames = []
    for index in range(count):
        target = workdir / f'frame-{index}.jpg'
        moment = duration * (index + 0.5) / count
        await _run('ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', *LOCAL_ONLY, '-ss', f'{moment:.3f}',
                   '-i', str(path), '-frames:v', '1', '-vf', f"scale='min({KEYFRAME_WIDTH},iw)':-2", '-q:v', '4',
                   '-y', str(target))
        if target.exists() and target.stat().st_size:
            frames.append(target)
    return frames
