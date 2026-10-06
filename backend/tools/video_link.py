"""Fetch a short public video from a link, so it can go through the same pipeline as an upload.

Uses yt-dlp, an open-source downloader, run as a separate process with a fixed argument list (no
shell) and a time limit. Only the single-video extractors of the sites listed here are enabled, so
playlists, profiles and every other site are refused, and live streams and long videos are filtered
out before anything is downloaded. No cookies, logins or browser data are used: a private post, or
one a site hides from signed-out visitors, cannot be fetched.

The process runs in its own empty folder with a short list of environment variables, so it sees none
of the app's keys and loads no code from the folder the app was started in. It and anything it starts
(ffmpeg, to join video and sound) are stopped together when the time runs out or the request ends,
and it cannot write files larger than twice the video size limit.

Downloading from these sites may be against their terms of service. The app keeps this feature off
unless ALLOW_VIDEO_LINKS is set, and it is meant for videos the person submitting has the right to
download.

  python -m tools.video_link URL     try a download and say what came back (no model or search calls)
"""
import asyncio
import importlib.util
import json
import os
import re
import signal
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

from tools.media import MAX_VIDEO_BYTES, MAX_VIDEO_SECONDS, MediaRejected

# Site name -> the hosts it is served from (subdomains included).
SITES = {'Instagram': ('instagram.com',), 'TikTok': ('tiktok.com',), 'YouTube': ('youtube.com', 'youtu.be')}
# yt-dlp's extractors for one video on those sites. Their playlist, profile and search extractors, and the
# generic extractor that would fetch any address, are left out.
EXTRACTORS = 'Instagram,TikTok,vm.tiktok,youtube'
# One file with sound when the site offers it, otherwise the best video and audio joined by ffmpeg.
FORMAT = 'b[height<=720]/bv*[height<=720]+ba/b/bv*+ba'
MAX_LINK_CHARS = 500
# What the downloader is allowed to inherit: where programs are, proxies, certificates and locale. Nothing else.
PASSED_ON = ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'TEMP', 'TMP', 'SYSTEMROOT', 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY',
             'http_proxy', 'https_proxy', 'no_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE')
MAX_CAPTION_CHARS = 2200
DOWNLOAD_TIMEOUT = 150

NOT_A_VIDEO_LINK = ('Paste a link to one public video on ' + ', '.join(SITES) + '. '
                    'It must start with https:// and point at a single post, not a profile or a playlist.')
TOO_LONG_OR_LIVE = (f'That video is longer than {MAX_VIDEO_SECONDS // 60} minutes, larger than {MAX_VIDEO_BYTES // (1024 * 1024)} MB, '
                    'a live stream, or not a single video, so it was not downloaded.')
COULD_NOT_DOWNLOAD = ('The video could not be downloaded from that link. The post may be private or removed, or the site may be '
                      'refusing automated downloads from this server. Download it yourself and upload the file instead.')
NEEDS_SIGN_IN = ('That site asked for a sign-in before showing the video, and this app never signs in to other sites. '
                 'Download the video yourself and upload the file instead.')
TOOK_TOO_LONG = 'Downloading the video took too long. Try again, or upload the file instead.'


class LinkRejected(MediaRejected):
    """The link is not one this app will try to download; the message is safe to show."""


class DownloadFailed(MediaRejected):
    """The download was attempted and did not produce a usable video; the message is safe to show."""


@dataclass(frozen=True)
class Downloaded:
    path: Path
    url: str      # the link as submitted, tidied
    site: str
    title: str
    caption: str  # the post's own caption or description, when the site gave one


def installed() -> bool:
    return importlib.util.find_spec('yt_dlp') is not None


def site_of(url: str) -> str | None:
    host = (urlsplit(url).hostname or '').lower().rstrip('.')
    for name, hosts in SITES.items():
        if any(host == known or host.endswith('.' + known) for known in hosts):
            return name
    return None


def check_link(url: str) -> str:
    """The link, tidied, when it is an https link to one of the supported sites; otherwise LinkRejected.

    The host is checked here so that no request is ever made to an address the person chose freely.
    Whether the link is a single video is left to the extractors, which refuse everything else.
    """
    url = (url or '').strip()
    if not url or len(url) > MAX_LINK_CHARS or '\\' in url or any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise LinkRejected(NOT_A_VIDEO_LINK)
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise LinkRejected(NOT_A_VIDEO_LINK)
    host = (parts.hostname or '').lower()
    # Plain host names only: programs disagree about where an unusual one ends, and this check must not be the odd one out.
    if (parts.scheme != 'https' or parts.username or parts.password or port not in (None, 443)
            or not re.fullmatch(r'[a-z0-9.-]+', host) or not site_of(url)):
        raise LinkRejected(NOT_A_VIDEO_LINK)
    # Only the video ID is kept from the query: sharing links carry tracking codes that identify who shared them.
    video_id = parse_qs(parts.query).get('v', [''])[0]
    query = f'v={video_id}' if re.fullmatch(r'[\w-]{1,32}', video_id) else ''
    if parts.path in ('', '/') and not query:
        raise LinkRejected(NOT_A_VIDEO_LINK)
    return urlunsplit((parts.scheme, host, parts.path, query, ''))


def command(url: str, workdir: Path) -> list[str]:
    """The fixed argument list for one download. The link is the last argument, after `--`."""
    return [sys.executable, '-m', 'yt_dlp',
            '--no-config', '--no-cache-dir', '--no-plugin-dirs',  # nothing from this machine's own yt-dlp settings or plugins
            '--use-extractors', EXTRACTORS, '--no-playlist', '--playlist-items', '1',
            '--match-filters', f'duration <=? {MAX_VIDEO_SECONDS} & !is_live',
            '--max-filesize', f'{MAX_VIDEO_BYTES // (1024 * 1024)}M',
            '--format', FORMAT, '--merge-output-format', 'mp4',
            '--socket-timeout', '20', '--retries', '2', '--fragment-retries', '2',
            '--quiet', '--no-warnings', '--no-progress', '--no-part', '--no-mtime', '--restrict-filenames',
            '--write-info-json', '--no-write-playlist-metafiles', '--no-write-comments',
            '--paths', str(workdir), '--output', 'video.%(ext)s',
            '--', url]


def _failure(returncode: int, errors: str) -> str:
    """A safe message for a download that produced no file. The tool's own output is never shown."""
    if returncode == 0:
        return TOO_LONG_OR_LIVE     # the filter skipped it, which is not an error to yt-dlp
    lowered = errors.lower()
    if 'max-filesize' in lowered or 'larger than' in lowered:
        return TOO_LONG_OR_LIVE
    if 'no suitable extractor' in lowered or 'unsupported url' in lowered:
        return NOT_A_VIDEO_LINK     # a profile, a playlist, or some other page on a supported site
    if any(sign in lowered for sign in ('sign in', 'log in', 'login', 'cookies', 'age-restricted', 'private')):
        return NEEDS_SIGN_IN
    return COULD_NOT_DOWNLOAD


async def download(url: str, workdir: Path) -> Downloaded:
    """Download the one video behind `url` into `workdir`. Raises LinkRejected or DownloadFailed."""
    url = check_link(url)
    if not installed():
        raise DownloadFailed('Checking a video from a link needs yt-dlp, which is not installed.')
    process = await asyncio.create_subprocess_exec(
        *command(url, workdir), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        cwd=workdir, env={name: os.environ[name] for name in PASSED_ON if name in os.environ},
        start_new_session=True, **({'preexec_fn': _cap_file_size} if os.name == 'posix' else {}))
    try:
        _, errors = await asyncio.wait_for(process.communicate(), timeout=DOWNLOAD_TIMEOUT)
    except asyncio.TimeoutError:
        raise DownloadFailed(TOOK_TOO_LONG)
    finally:
        # Reached on success, timeout and cancellation alike: nothing the download started may outlive it.
        await _stop(process)
    return collect(url, workdir, process.returncode, errors.decode('utf-8', 'replace'))


def _cap_file_size() -> None:
    """Runs in the new process before yt-dlp starts: no file it or ffmpeg writes may pass twice the video limit.
    The size option yt-dlp offers does not cover every kind of download."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_FSIZE, (2 * MAX_VIDEO_BYTES, 2 * MAX_VIDEO_BYTES))
    except (ImportError, ValueError, OSError):
        pass


async def _stop(process) -> None:
    """Stop the downloader and everything it started. It runs in its own session, so its group is only its own."""
    try:
        if hasattr(os, 'killpg'):
            os.killpg(process.pid, signal.SIGKILL)   # also anything a finished downloader left running
        elif process.returncode is None:
            process.kill()
    except (ProcessLookupError, PermissionError):
        pass
    await process.wait()


def collect(url: str, workdir: Path, returncode: int, errors: str = '') -> Downloaded:
    """What a finished download left in `workdir`, or DownloadFailed with the reason."""
    # A finished download is exactly `video.<extension>`. Pieces of an unfinished one have other names
    # (video.f137.mp4, video.mp4.part), and a failed run can leave a cut-off file under the final name.
    videos = [p for p in sorted(workdir.glob('video.*')) if p.is_file() and re.fullmatch(r'video\.[A-Za-z0-9]+', p.name)
              and p.suffix != '.json']
    if returncode != 0 or not videos:
        raise DownloadFailed(_failure(returncode, errors))
    path = videos[0]
    if path.stat().st_size > MAX_VIDEO_BYTES:
        raise DownloadFailed(f'That video is larger than {MAX_VIDEO_BYTES // (1024 * 1024)} MB.')
    info = {}
    try:
        loaded = json.loads((workdir / 'video.info.json').read_text(encoding='utf-8'))
        info = loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        pass  # the video is usable without its description
    text = lambda key: ' '.join(str(info.get(key) or '').split())
    return Downloaded(path=path, url=url, site=site_of(url), title=text('title')[:120] or 'video',
                      caption=text('description')[:MAX_CAPTION_CHARS])


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    with tempfile.TemporaryDirectory(prefix='reel-link-') as work:
        try:
            got = asyncio.run(download(argv[0], Path(work)))
        except MediaRejected as exc:
            print(f'Not downloaded: {exc}')
            return 1
        print(f'Downloaded from {got.site}: {got.path.stat().st_size / 1e6:.1f} MB, {got.path.suffix} file, '
              f'title "{got.title}", caption of {len(got.caption)} characters. The file was deleted again.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
