"""A video given as a link: which links are accepted, how the downloader is run, and the API around it.
No network: the downloader is never started for real."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
import main
from services.video import LINK_NOTE, run_video_link_pipeline
from tools import video_link
from tools.media import MAX_VIDEO_BYTES, MediaRejected
from tools.video_link import DownloadFailed, Downloaded, LinkRejected, check_link, collect, command
from tests.test_video import SPOKEN, FakeTranscriber, VideoProvider, fetch, make_clip, needs_ffmpeg


@pytest.mark.parametrize('url,site', [
    ('https://www.instagram.com/reel/Cabc123/', 'Instagram'),
    ('https://instagram.com/p/Cabc123/', 'Instagram'),
    ('https://www.tiktok.com/@someone/video/7212345678901234567', 'TikTok'),
    ('https://vm.tiktok.com/ZMabcdef/', 'TikTok'),
    ('https://www.youtube.com/shorts/abcdefghijk', 'YouTube'),
    ('https://youtu.be/abcdefghijk', 'YouTube'),
    ('https://m.youtube.com/watch?v=abcdefghijk', 'YouTube'),
])
def test_links_to_the_supported_sites_are_accepted(url, site):
    assert check_link(f'  {url}  ') == url and video_link.site_of(url) == site


def test_the_link_is_tidied_and_sharing_codes_are_dropped():
    assert check_link('https://WWW.YouTube.com/shorts/abc#t=3') == 'https://www.youtube.com/shorts/abc'
    # Sharing links carry codes that identify who shared them; only a YouTube video ID is kept from the query.
    assert check_link('https://www.instagram.com/reel/Cabc123/?igsh=MWabc&utm_source=ig') == 'https://www.instagram.com/reel/Cabc123/'
    assert check_link('https://youtu.be/abcdefghijk?si=TRACKING') == 'https://youtu.be/abcdefghijk'
    assert check_link('https://www.youtube.com/watch?si=x&v=abc-_123&feature=share') == 'https://www.youtube.com/watch?v=abc-_123'
    with pytest.raises(LinkRejected):
        check_link('https://www.youtube.com/?si=only-tracking')


@pytest.mark.parametrize('url', [
    '', '   ', 'not a link', 'youtube.com/shorts/abc',
    'http://www.youtube.com/shorts/abc',                    # not https
    'ftp://www.youtube.com/shorts/abc', 'file:///etc/passwd', 'javascript:alert(1)',
    'https://example.com/video.mp4',                        # any other site
    'https://youtube.com.example.net/shorts/abc',           # a lookalike
    'https://notyoutube.com/shorts/abc', 'https://evil.example/?u=https://www.youtube.com/shorts/abc',
    'https://127.0.0.1/shorts/abc', 'https://localhost/reel/abc', 'https://169.254.169.254/latest/meta-data',
    'https://user:pass@www.youtube.com/shorts/abc',         # credentials in the link
    'https://evil.net\\.youtube.com/watch?v=abc', 'https://evil.net\\www.youtube.com/shorts/abc',   # read differently by different programs
    'https://evil.net%2f.youtube.com/shorts/abc', 'https://evil.net%23.youtube.com/shorts/abc', "https://a'b.youtube.com/shorts/abc",
    'https://[::1]/shorts/abc', 'https://youtube.com@evil.net/shorts/abc', 'https://evil.net#@youtube.com/shorts/abc',
    'https://www.youtube.com:8443/shorts/abc',              # another port
    'https://www.youtube.com:notaport/shorts/abc',
    'https://www.youtube.com/', 'https://www.instagram.com',   # the site itself, not a post
    'https://www.youtube.com/shorts/abc def', 'https://www.youtube.com/shorts/abc\n--exec',
    'https://www.youtube.com/shorts/' + 'a' * 600,
])
def test_everything_else_is_refused_before_any_request_is_made(url):
    with pytest.raises(LinkRejected):
        check_link(url)


def test_the_downloader_is_run_from_a_fixed_list_with_the_link_last(tmp_path):
    url = 'https://www.youtube.com/shorts/abc'
    args = command(url, tmp_path)
    assert args[:3] == [sys.executable, '-m', 'yt_dlp'] and args[-2:] == ['--', url]
    option = lambda name: args[args.index(name) + 1]
    # Only single-video extractors: no playlists, profiles or the generic "any address" extractor.
    assert option('--use-extractors') == 'Instagram,TikTok,vm.tiktok,youtube' and '--no-playlist' in args
    assert option('--match-filters') == 'duration <=? 180 & !is_live' and option('--max-filesize') == '100M'
    assert option('--paths') == str(tmp_path) and option('--output') == 'video.%(ext)s'
    # Nothing from the machine it runs on: no settings file, no cache, and never cookies or a login.
    assert '--no-config' in args and '--no-cache-dir' in args and '--no-plugin-dirs' in args
    assert not any(a.startswith(('--cookies', '--username', '--password', '--netrc', '--exec')) for a in args)


def test_a_link_that_looks_like_an_option_cannot_become_one(tmp_path):
    with pytest.raises(LinkRejected):
        check_link('--exec=rm -rf /')
    args = command('https://www.youtube.com/watch?v=--exec', tmp_path)
    assert args.index('--') == len(args) - 2


def test_a_finished_download_is_collected_with_its_caption(tmp_path):
    (tmp_path / 'video.mp4').write_bytes(b'x' * 10)
    (tmp_path / 'video.info.json').write_text(json.dumps({'title': 'A  reel\nabout Lake Arlo', 'description': ' Lake Arlo\n never freezes. ' + 'x' * 5000}))
    got = collect('https://www.instagram.com/reel/abc/', tmp_path, 0)
    assert got.path.name == 'video.mp4' and got.site == 'Instagram' and got.title == 'A reel about Lake Arlo'
    assert got.caption.startswith('Lake Arlo never freezes.') and len(got.caption) == 2200


@pytest.mark.parametrize('metadata', ['not json', '[1, 2]', '"text"', None])
def test_a_download_without_a_usable_description_is_still_usable(tmp_path, metadata):
    (tmp_path / 'video.webm').write_bytes(b'x')
    if metadata is not None:
        (tmp_path / 'video.info.json').write_text(metadata)
    got = collect('https://youtu.be/abc', tmp_path, 0)
    assert got.path.name == 'video.webm' and got.title == 'video' and got.caption == ''


@pytest.mark.parametrize('leftover', ['video.f137.mp4', 'video.mp4.part', 'video.mp4-Frag3', 'video.mp4.ytdl', 'video.webp.json', 'other.mp4'])
def test_pieces_of_an_unfinished_download_are_not_mistaken_for_the_video(tmp_path, leftover):
    (tmp_path / leftover).write_bytes(b'x' * 10)
    with pytest.raises(DownloadFailed):
        collect('https://youtu.be/abc', tmp_path, 0)


def test_a_file_left_by_a_failed_download_is_not_used(tmp_path):
    """The downloader writes under the final name as it goes, so a failed run can leave a cut-off video.mp4."""
    (tmp_path / 'video.mp4').write_bytes(b'x' * 10)
    with pytest.raises(DownloadFailed) as failure:
        collect('https://youtu.be/abc', tmp_path, 1, 'ERROR: unable to download video data: HTTP Error 403')
    assert str(failure.value) == video_link.COULD_NOT_DOWNLOAD


@pytest.mark.parametrize('returncode,errors,message', [
    (0, '', video_link.TOO_LONG_OR_LIVE),                                             # the filter skipped it
    (1, 'ERROR: [Instagram] abc: Requested content is not available, rate-limit reached or login required', video_link.NEEDS_SIGN_IN),
    (1, "ERROR: [youtube] abc: Sign in to confirm you're not a bot", video_link.NEEDS_SIGN_IN),
    (1, 'ERROR: [youtube] abc: Private video', video_link.NEEDS_SIGN_IN),
    (1, 'ERROR: File is larger than max-filesize (150000000 bytes > 104857600 bytes). Aborting.', video_link.TOO_LONG_OR_LIVE),
    (1, 'ERROR: No suitable extractor found for URL https://www.youtube.com/playlist?list=1', video_link.NOT_A_VIDEO_LINK),
    (1, 'ERROR: [TikTok] 1: Unable to download webpage: HTTP Error 403: Forbidden /home/app/secret', video_link.COULD_NOT_DOWNLOAD),
])
def test_a_failed_download_gets_a_plain_reason_and_never_the_tools_own_output(tmp_path, returncode, errors, message):
    (tmp_path / 'video.info.json').write_text('{}')   # metadata alone is not a video
    with pytest.raises(DownloadFailed) as failure:
        collect('https://youtu.be/abc', tmp_path, returncode, errors)
    assert str(failure.value) == message and 'ERROR' not in message and 'secret' not in message


def test_a_download_larger_than_the_limit_is_refused(tmp_path, monkeypatch):
    (tmp_path / 'video.mp4').write_bytes(b'x' * 10)
    monkeypatch.setattr(video_link, 'MAX_VIDEO_BYTES', 5)
    with pytest.raises(DownloadFailed):
        collect('https://youtu.be/abc', tmp_path, 0)


def test_a_download_that_hangs_is_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(video_link, 'installed', lambda: True)
    monkeypatch.setattr(video_link, 'command', lambda url, workdir: [sys.executable, '-c', 'import time; time.sleep(30)'])
    monkeypatch.setattr(video_link, 'DOWNLOAD_TIMEOUT', 0.5)
    with pytest.raises(DownloadFailed) as failure:
        asyncio.run(video_link.download('https://youtu.be/abc', tmp_path))
    assert str(failure.value) == video_link.TOOK_TOO_LONG


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A killed child that nobody has waited for yet still has a process entry.
    try:
        return 'Z' not in Path(f'/proc/{pid}/stat').read_text().split(')')[-1].split()[0]
    except OSError:
        return True


# A stand-in downloader that starts a child of its own (as yt-dlp starts ffmpeg), records both IDs, then hangs.
FAMILY = ("import os, subprocess, sys, time, pathlib; "
          "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
          "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()} {child.pid}'); time.sleep(60)")


def family(tmp_path, monkeypatch):
    record = tmp_path / 'pids.txt'
    monkeypatch.setattr(video_link, 'installed', lambda: True)
    monkeypatch.setattr(video_link, 'command', lambda url, workdir: [sys.executable, '-c', FAMILY, str(record)])
    return record


@pytest.mark.skipif(os.name != 'posix', reason='process groups')
def test_a_timeout_stops_the_downloader_and_whatever_it_started(tmp_path, monkeypatch):
    record = family(tmp_path, monkeypatch)
    monkeypatch.setattr(video_link, 'DOWNLOAD_TIMEOUT', 1.5)
    with pytest.raises(DownloadFailed):
        asyncio.run(video_link.download('https://youtu.be/abc', tmp_path))
    parent, child = map(int, record.read_text().split())
    time.sleep(0.3)
    assert not alive(parent) and not alive(child)


@pytest.mark.skipif(os.name != 'posix', reason='process groups')
def test_a_cancelled_request_stops_the_downloader_too(tmp_path, monkeypatch):
    record = family(tmp_path, monkeypatch)
    async def scenario():
        task = asyncio.create_task(video_link.download('https://youtu.be/abc', tmp_path))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if record.exists() and record.read_text().count(' '):
                break
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    parent, child = map(int, record.read_text().split())
    time.sleep(0.3)
    assert not alive(parent) and not alive(child)


def test_the_downloader_gets_none_of_the_apps_secrets_and_runs_in_its_own_folder(tmp_path, monkeypatch):
    work = tmp_path / 'work'
    work.mkdir()
    script = ("import json, os, pathlib; pathlib.Path('seen.json').write_text(json.dumps({'cwd': os.getcwd(), 'env': sorted(os.environ)})); "
              "pathlib.Path('video.mp4').write_bytes(b'x')")
    monkeypatch.setenv('OPENAI_API_KEY', 'sk-not-for-the-downloader')
    monkeypatch.setenv('TAVILY_API_KEY', 'tvly-not-for-the-downloader')
    monkeypatch.setenv('SESSION_SECRET', 's' * 40)
    monkeypatch.setenv('HTTPS_PROXY', 'http://proxy.example:8080')
    monkeypatch.setattr(video_link, 'installed', lambda: True)
    monkeypatch.setattr(video_link, 'command', lambda url, workdir: [sys.executable, '-c', script])
    asyncio.run(video_link.download('https://youtu.be/abc', work))
    seen = json.loads((work / 'seen.json').read_text())
    assert Path(seen['cwd']).resolve() == work.resolve()
    assert not {'OPENAI_API_KEY', 'TAVILY_API_KEY', 'SESSION_SECRET'} & set(seen['env'])
    assert 'HTTPS_PROXY' in seen['env'] and 'PATH' in seen['env']


@pytest.mark.skipif(os.name != 'posix', reason='resource limits')
def test_the_downloader_cannot_fill_the_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(video_link, 'MAX_VIDEO_BYTES', 50_000)
    script = "open('video.mp4', 'wb').write(b'x' * 500_000)"
    monkeypatch.setattr(video_link, 'installed', lambda: True)
    monkeypatch.setattr(video_link, 'command', lambda url, workdir: [sys.executable, '-c', script])
    with pytest.raises(DownloadFailed):
        asyncio.run(video_link.download('https://youtu.be/abc', tmp_path))
    assert (tmp_path / 'video.mp4').stat().st_size <= 100_000


def test_without_the_downloader_installed_nothing_is_started(tmp_path, monkeypatch):
    monkeypatch.setattr(video_link, 'installed', lambda: False)
    monkeypatch.setattr(video_link, 'command', lambda *a: pytest.fail('the downloader must not be started'))
    with pytest.raises(DownloadFailed):
        asyncio.run(video_link.download('https://youtu.be/abc', tmp_path))


def test_the_download_step_runs_the_command_and_collects_the_file(tmp_path, monkeypatch):
    """A stand-in for yt-dlp that writes the files yt-dlp would."""
    script = ("import json, pathlib, sys; d = pathlib.Path(sys.argv[1]); (d / 'video.mp4').write_bytes(b'x' * 20); "
              "(d / 'video.info.json').write_text(json.dumps({'title': 'T', 'description': 'D'}))")
    monkeypatch.setattr(video_link, 'installed', lambda: True)
    monkeypatch.setattr(video_link, 'command', lambda url, workdir: [sys.executable, '-c', script, str(workdir)])
    got = asyncio.run(video_link.download('https://youtu.be/abc', tmp_path))
    assert got == Downloaded(path=tmp_path / 'video.mp4', url='https://youtu.be/abc', site='YouTube', title='T', caption='D')


# ---- the pipeline -------------------------------------------------------------------------------

def downloader(clip, caption='', seen=None):
    async def download(url, workdir):
        target = Path(workdir) / 'video.mp4'
        target.write_bytes(clip.read_bytes())
        if seen is not None:
            seen.update(url=url, path=target)
        return Downloaded(path=target, url=url, site='Instagram', title='A reel', caption=caption)
    return download


def run_link(clip, provider, caption='', post_caption='', seen=None):
    return asyncio.run(run_video_link_pipeline('https://www.instagram.com/reel/abc/', caption, provider, FakeTranscriber(), fetch,
                                               downloader(clip, post_caption, seen)))


@needs_ffmpeg
def test_a_linked_video_goes_through_the_same_pipeline_as_an_upload(tmp_path):
    seen = {}
    report = run_link(make_clip(tmp_path / 'clip.mp4'), VideoProvider(), seen=seen)
    assert report.input_type == 'video' and [c.claim for c in report.claims] == [SPOKEN]
    assert report.source_url == 'https://www.instagram.com/reel/abc/' and report.submitted_text == 'Video link: https://www.instagram.com/reel/abc/'
    assert LINK_NOTE in report.limitations and report.media.had_audio
    assert not seen['path'].exists()   # the download is deleted with its folder


@needs_ffmpeg
def test_the_posts_own_caption_is_used_unless_one_was_typed(tmp_path):
    clip = make_clip(tmp_path / 'clip.mp4')
    provider = VideoProvider()
    run_link(clip, provider, post_caption='Posted caption about Lake Arlo.')
    assert 'Posted caption about Lake Arlo.' in provider.extraction_input
    provider = VideoProvider()
    run_link(clip, provider, caption='Typed caption.', post_caption='Posted caption about Lake Arlo.')
    assert 'Typed caption.' in provider.extraction_input and 'Posted caption' not in provider.extraction_input


def test_a_failed_download_reaches_the_caller_and_starts_no_research():
    async def download(url, workdir):
        raise DownloadFailed(video_link.COULD_NOT_DOWNLOAD)
    provider = VideoProvider()
    with pytest.raises(MediaRejected):
        asyncio.run(run_video_link_pipeline('https://youtu.be/abc', '', provider, FakeTranscriber(), fetch, download))
    assert provider.queries == [] and provider.extraction_input is None


# ---- the API ------------------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    for key in ('OPENAI_API_KEY', 'TAVILY_API_KEY'):
        monkeypatch.setenv(key, 'offline-test')
    monkeypatch.setenv('OPENAI_MODEL', 'gpt-4.1-mini')
    monkeypatch.setenv('ENABLE_LIVE_RESEARCH', 'true')
    monkeypatch.delenv('ALLOW_VIDEO_LINKS', raising=False)
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(main, 'video_status', lambda: (True, 'ready'))
    monkeypatch.setattr(main.video_link, 'installed', lambda: True)
    class Stub:
        async def close(self):
            pass
    monkeypatch.setattr(main, 'Providers', Stub)
    seen = {}
    async def pipeline(url, caption, provider, transcriber):
        from tests.test_history import live_report
        seen.update(url=url, caption=caption)
        return live_report(f'Video link: {url}').model_copy(update={'input_type': 'video', 'source_url': url})
    monkeypatch.setattr(main, 'run_video_link_pipeline', pipeline)
    with TestClient(main.app) as client:
        client.seen = seen
        yield client


LINK = {'url': 'https://www.instagram.com/reel/abc/'}


def test_links_are_off_unless_the_deployment_turns_them_on(client):
    body = client.get('/config').json()
    assert body['video_ready'] is True and body['video_link_ready'] is False and 'turned off' in body['video_link_message']
    assert body['video_link_sites'] == ['Instagram', 'TikTok', 'YouTube']
    response = client.post('/fact-check-video-link', json=LINK)
    assert response.status_code == 503 and 'turned off' in response.json()['detail'] and client.seen == {}


def test_a_link_is_checked_and_the_report_saved_when_links_are_on(client, monkeypatch):
    monkeypatch.setenv('ALLOW_VIDEO_LINKS', 'true')
    assert client.get('/config').json()['video_link_ready'] is True
    response = client.post('/fact-check-video-link', json={'url': '  https://www.instagram.com/reel/abc/#x ', 'caption': 'A caption'})
    assert response.status_code == 200 and client.seen == {'url': 'https://www.instagram.com/reel/abc/', 'caption': 'A caption'}
    saved = client.get(f"/history/{response.json()['id']}").json()
    assert saved['input_type'] == 'video' and saved['source_url'] == 'https://www.instagram.com/reel/abc/'


@pytest.mark.parametrize('body,status', [
    ({'url': 'https://example.com/video.mp4'}, 422), ({'url': 'http://www.youtube.com/shorts/abc'}, 422),
    ({'url': 'https://127.0.0.1/reel/abc'}, 422), ({'url': ''}, 422), ({}, 422), ({'url': 'https://youtu.be/abc', 'extra': 1}, 422),
    ({'url': 'https://youtu.be/abc', 'caption': 'x' * 2201}, 422),
])
def test_bad_links_are_refused_before_anything_runs(client, monkeypatch, body, status):
    monkeypatch.setenv('ALLOW_VIDEO_LINKS', 'true')
    assert client.post('/fact-check-video-link', json=body).status_code == status and client.seen == {}


def test_links_need_the_downloader_and_the_video_tools(client, monkeypatch):
    monkeypatch.setenv('ALLOW_VIDEO_LINKS', 'true')
    monkeypatch.setattr(main.video_link, 'installed', lambda: False)
    response = client.post('/fact-check-video-link', json=LINK)
    assert response.status_code == 503 and 'yt-dlp' in response.json()['detail']
    monkeypatch.setattr(main, 'video_status', lambda: (False, 'Video checks need ffmpeg.'))
    assert client.post('/fact-check-video-link', json=LINK).json()['detail'] == 'Video checks need ffmpeg.'


def test_a_download_failure_is_reported_in_plain_words(client, monkeypatch):
    monkeypatch.setenv('ALLOW_VIDEO_LINKS', 'true')
    async def pipeline(url, caption, provider, transcriber):
        raise DownloadFailed(video_link.NEEDS_SIGN_IN)
    monkeypatch.setattr(main, 'run_video_link_pipeline', pipeline)
    response = client.post('/fact-check-video-link', json=LINK)
    assert response.status_code == 422 and response.json()['detail'] == video_link.NEEDS_SIGN_IN


def test_the_link_route_is_behind_the_same_sign_in_as_the_others(client, monkeypatch):
    monkeypatch.setenv('ALLOW_VIDEO_LINKS', 'true')
    monkeypatch.setenv('APP_PASSWORD', 'a-long-test-password')
    monkeypatch.setenv('SESSION_SECRET', 's' * 40)
    assert client.post('/fact-check-video-link', json=LINK).status_code == 401 and client.seen == {}
