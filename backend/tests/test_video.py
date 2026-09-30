"""Video mode: real ffmpeg on tiny generated clips (skipped when ffmpeg is absent), fakes for
transcription and the model. No network or paid calls."""
import asyncio
import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
import main
from schemas import AtomicClaim, Extraction, ScreenText
from services import media
from services.budget import Budget, IMAGE_TOKENS
from services.media import MediaRejected, MediaToolMissing
from services.providers import Providers
from services.transcribe import Transcript
from services.video import MAX_CAPTION_CHARS, compose, run_video_pipeline
from tests.test_pipeline import FakeProvider, PAGE

needs_ffmpeg = pytest.mark.skipif(not media.tools_available(), reason='ffmpeg is not installed')
SPOKEN = 'The fictional Lake Arlo is 300 metres deep and never freezes.'
EVIDENCE_URL = 'https://evidence.example.org/report'


def make_clip(path: Path, seconds=3, audio=True, video=True):
    args = ['ffmpeg', '-nostdin', '-loglevel', 'error', '-y']
    if video:
        args += ['-f', 'lavfi', '-i', f'testsrc=duration={seconds}:size=320x240:rate=10']
    if audio:
        args += ['-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}']
    args += (['-c:v', 'mpeg4'] if video else []) + (['-c:a', 'aac'] if audio else []) + [str(path)]
    subprocess.run(args, check=True)
    return path


@pytest.fixture
def clip(tmp_path):
    return make_clip(tmp_path / 'clip.mp4')


# ---- media ---------------------------------------------------------------------------------

@needs_ffmpeg
def test_probe_reads_duration_and_audio(clip, tmp_path):
    info = asyncio.run(media.probe(clip))
    assert 2.5 < info.duration < 3.5 and info.has_audio
    silent = asyncio.run(media.probe(make_clip(tmp_path / 'silent.mp4', audio=False)))
    assert not silent.has_audio


@needs_ffmpeg
def test_audio_only_and_non_video_files_are_rejected(tmp_path):
    with pytest.raises(MediaRejected, match='no video track'):
        asyncio.run(media.probe(make_clip(tmp_path / 'audio.mp4', video=False)))
    fake = tmp_path / 'fake.mp4'
    fake.write_text('not a video')
    with pytest.raises(MediaRejected, match='could not be read'):
        asyncio.run(media.probe(fake))


@needs_ffmpeg
def test_duration_and_size_limits(clip, monkeypatch):
    monkeypatch.setattr(media, 'MAX_VIDEO_SECONDS', 1)
    with pytest.raises(MediaRejected, match='at most'):
        asyncio.run(media.probe(clip))
    monkeypatch.setattr(media, 'MAX_VIDEO_BYTES', 10)
    with pytest.raises(MediaRejected, match='MB'):
        asyncio.run(media.probe(clip))


@needs_ffmpeg
def test_playlists_cannot_make_ffmpeg_fetch_urls(tmp_path):
    playlist = tmp_path / 'clip.mp4'
    playlist.write_text('#EXTM3U\n#EXTINF:1,\nhttp://127.0.0.1:9/segment.ts\n#EXT-X-ENDLIST\n')
    with pytest.raises(MediaRejected):
        asyncio.run(media.probe(playlist))


@needs_ffmpeg
def test_audio_and_keyframes_are_extracted(clip, tmp_path):
    audio = asyncio.run(media.extract_audio(clip, tmp_path))
    info = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=sample_rate,channels', '-of', 'json',
                                      str(audio)], capture_output=True, check=True).stdout)['streams'][0]
    assert info['sample_rate'] == '16000' and info['channels'] == 1
    frames = asyncio.run(media.extract_keyframes(clip, tmp_path, 3.0))
    assert len(frames) == media.KEYFRAMES
    assert all(frame.read_bytes()[:2] == b'\xff\xd8' for frame in frames)


def test_missing_tools_are_reported(monkeypatch, tmp_path):
    monkeypatch.setattr('services.media.shutil.which', lambda name: None)
    path = tmp_path / 'x.mp4'
    path.write_bytes(b'x')
    with pytest.raises(MediaToolMissing):
        asyncio.run(media.probe(path))


# ---- pipeline ------------------------------------------------------------------------------

class FakeTranscriber:
    def __init__(self, text=SPOKEN):
        self.text, self.calls = text, 0
    async def transcribe(self, audio):
        self.calls += 1
        assert audio.exists() and audio.suffix == '.wav'
        return Transcript(text=self.text, language='en')


class VideoProvider(FakeProvider):
    def __init__(self, claims=None, screen='', **kwargs):
        super().__init__(**kwargs)
        self.video_claims, self.screen = claims, screen
        self.images, self.extraction_input = None, None
    async def search(self, query):
        self.queries.append(query)
        return [{'url': EVIDENCE_URL, 'title': 'Result'}]
    async def read_images(self, schema, instructions, images):
        assert schema is ScreenText
        self.images = images
        return ScreenText(text=self.screen)
    async def structured(self, schema, instructions, data):
        if schema is Extraction:
            self.extraction_input = json.loads(data)['text']
            claims = self.video_claims if self.video_claims is not None else [AtomicClaim(text=SPOKEN, context='')]
            return Extraction(intent='FACTUAL', claims=claims, omitted_claims=False, note='')
        return await super().structured(schema, instructions, data)


async def fetch(url):
    return url, PAGE


def run(clip, provider, transcriber=None, caption=''):
    return asyncio.run(run_video_pipeline(clip, 'clip.mp4', caption, provider, transcriber or FakeTranscriber(), fetch))


@needs_ffmpeg
def test_video_claims_come_from_the_transcript_and_are_researched(clip):
    provider = VideoProvider(screen='300 m DEEP')
    report = run(clip, provider, caption='Nature facts')
    assert report.input_type == 'video' and report.coverage_status == 'passed'
    assert [c.claim for c in report.claims] == [SPOKEN] and report.claims[0].sources_checked == 1
    assert report.source_text == compose(SPOKEN, '300 m DEEP', 'Nature facts') == provider.extraction_input
    assert report.media.frames_read == media.KEYFRAMES and report.media.had_audio
    assert report.media.transcript_language == 'en' and report.source_sha256
    assert len(provider.images) == media.KEYFRAMES and all(image[:2] == b'\xff\xd8' for image in provider.images)


@needs_ffmpeg
def test_silent_video_uses_on_screen_text_without_transcribing(tmp_path):
    silent = make_clip(tmp_path / 'silent.mp4', audio=False)
    transcriber = FakeTranscriber()
    claim = 'Lake Arlo never freezes'
    report = run(silent, VideoProvider(claims=[AtomicClaim(text=claim, context='')], screen=claim), transcriber)
    assert transcriber.calls == 0 and not report.media.had_audio
    assert report.claims[0].claim == claim


@needs_ffmpeg
def test_video_with_nothing_to_check_makes_no_model_call(tmp_path):
    silent = make_clip(tmp_path / 'silent.mp4', audio=False)
    provider = VideoProvider(screen='')
    report = run(silent, provider, FakeTranscriber(text=''))
    assert report.claims == [] and provider.extraction_input is None and provider.queries == []
    assert 'nothing to check' in report.note


@needs_ffmpeg
def test_claims_not_in_the_video_are_refused(clip):
    provider = VideoProvider(claims=[AtomicClaim(text='Lake Arlo is the deepest lake on Earth', context='')])
    report = run(clip, provider)
    assert report.coverage_status == 'incomplete' and report.claims[0].withheld_reason == 'coverage_failed'
    assert provider.queries == []


@needs_ffmpeg
def test_temporary_media_is_deleted_and_caption_is_capped(clip):
    before = set(Path(tempfile.gettempdir()).glob('reel-video-*'))
    report = run(clip, VideoProvider(), caption='x' * 5000)
    assert set(Path(tempfile.gettempdir()).glob('reel-video-*')) == before
    assert report.media.caption_chars == MAX_CAPTION_CHARS


# ---- model image input and budgets -----------------------------------------------------------

class ImageResponses:
    def __init__(self):
        self.kwargs = None
    async def parse(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_parsed=ScreenText(text='TEXT'), usage=SimpleNamespace(input_tokens=500, output_tokens=20))


def test_image_calls_send_low_detail_frames_and_are_budgeted(monkeypatch, tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY', 'offline-test')
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    provider = Providers()
    provider.client = SimpleNamespace(responses=ImageResponses())
    provider.spending = Budget(tmp_path / 'day.json', max_usd='0.50')
    result = asyncio.run(provider.read_images(ScreenText, 'read', [b'\xff\xd8one', b'\xff\xd8two']))
    assert result.text == 'TEXT' and provider.usage['model_calls'] == 1
    images = [part for part in provider.client.responses.kwargs['input'][0]['content'] if part['type'] == 'input_image']
    assert len(images) == 2 and all(i['detail'] == 'low' and i['image_url'].startswith('data:image/jpeg;base64,') for i in images)
    assert provider.spending.reserved == 500 * provider.spending.input_price + 20 * provider.spending.output_price


def test_image_reservation_covers_every_frame():
    budget = Budget(max_usd='1.00')
    with_images = budget.reserve('read', '', ScreenText, extra_input_tokens=IMAGE_TOKENS * 4)
    without = Budget(max_usd='1.00').reserve('read', '', ScreenText)
    assert with_images - without == IMAGE_TOKENS * 4 * budget.input_price


def test_validation_runner_traces_and_budgets_image_calls(monkeypatch):
    from evaluation.live import AuditProvider
    monkeypatch.setenv('OPENAI_API_KEY', 'offline-test')
    async def fake_read(self, schema, instructions, images):
        self.usage['model_calls'] += 1
        return ScreenText(text='seen')
    monkeypatch.setattr(Providers, 'read_images', fake_read)
    trace, budget = {'model': [], 'searches': []}, Budget(max_usd='0.10')
    asyncio.run(AuditProvider(budget, trace).read_images(ScreenText, 'read', [b'a', b'b']))
    assert trace['model'] == [{'stage': 'ScreenText', 'input': {'images': 2}, 'output': {'text': 'seen'}}]
    assert budget.reserved > 0
    trace, stopped = {'model': [], 'searches': []}, Budget(max_usd='0.10')
    stopped.stop()
    with pytest.raises(Exception, match='budget stopped'):
        asyncio.run(AuditProvider(stopped, trace).read_images(ScreenText, 'read', [b'a']))
    assert trace['blocked'] == [{'stage': 'ScreenText', 'reason': 'budget_stopped'}]


# ---- API -----------------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    for key in ('OPENAI_API_KEY', 'TAVILY_API_KEY'):
        monkeypatch.setenv(key, 'offline-test')
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    monkeypatch.setenv('ENABLE_LIVE_RESEARCH', 'true')
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    class Stub:
        async def close(self):
            pass
    monkeypatch.setattr(main, 'Providers', Stub)
    with TestClient(main.app) as client:
        yield client


def test_video_needs_its_tools_and_says_how_to_install_them(client, monkeypatch):
    # ffmpeg present (CI runners may lack it), transcription missing: the message names the missing piece.
    monkeypatch.setattr(main.media, 'tools_available', lambda: True)
    monkeypatch.setattr(main.LocalWhisper, 'installed', staticmethod(lambda: False))
    body = client.get('/config').json()
    assert body['video_ready'] is False and 'requirements-video.txt' in body['video_message']
    response = client.post('/fact-check-video', files={'file': ('clip.mp4', b'data', 'video/mp4')})
    assert response.status_code == 503 and 'requirements-video.txt' in response.json()['detail']


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setattr(main, 'video_status', lambda: (True, 'ready'))
    seen = {}
    async def pipeline(path, name, caption, provider, transcriber):
        from tests.test_history import live_report
        seen.update(path=path, exists=path.exists(), size=path.stat().st_size, name=name, caption=caption)
        return live_report(f'Video: {name}').model_copy(update={'input_type': 'video'})
    monkeypatch.setattr(main, 'run_video_pipeline', pipeline)
    return seen


def test_upload_is_processed_saved_to_history_and_deleted(client, ready):
    response = client.post('/fact-check-video', files={'file': ('../../My Reel.MP4', b'x' * 2048, 'video/mp4')},
                           data={'caption': 'A caption'})
    assert response.status_code == 200
    assert ready['exists'] and ready['size'] == 2048 and ready['name'] == 'My Reel.MP4' and ready['caption'] == 'A caption'
    assert not ready['path'].exists()
    assert client.get(f"/history/{response.json()['id']}").json()['input_type'] == 'video'


@pytest.mark.parametrize('name', ['notes.txt', 'clip.exe', 'clip'])
def test_non_video_filenames_are_refused(client, ready, name):
    assert client.post('/fact-check-video', files={'file': (name, b'x', 'video/mp4')}).status_code == 422
    assert not ready


def test_oversized_uploads_are_refused_early_and_while_streaming(client, ready, monkeypatch):
    monkeypatch.setattr(media, 'MAX_VIDEO_BYTES', 1000)
    response = client.post('/fact-check-video', files={'file': ('clip.mp4', b'x' * 5000, 'video/mp4')})
    assert response.status_code == 413 and not ready
    response = client.post('/fact-check-video', files={'file': ('clip.mp4', b'x' * (3 * 1024 * 1024), 'video/mp4')})
    assert response.status_code == 413


def test_long_captions_are_refused(client, ready):
    response = client.post('/fact-check-video', files={'file': ('clip.mp4', b'x', 'video/mp4')},
                           data={'caption': 'x' * (MAX_CAPTION_CHARS + 1)})
    assert response.status_code == 422


def test_media_errors_become_clear_messages(client, monkeypatch):
    monkeypatch.setattr(main, 'video_status', lambda: (True, 'ready'))
    async def rejected(*args):
        raise MediaRejected('Videos must be at most 3 minutes long.')
    monkeypatch.setattr(main, 'run_video_pipeline', rejected)
    response = client.post('/fact-check-video', files={'file': ('clip.mp4', b'x', 'video/mp4')})
    assert response.status_code == 422 and response.json()['detail'] == 'Videos must be at most 3 minutes long.'
