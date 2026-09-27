#!/usr/bin/env python3
"""Start the local demo with one command. Uses no API keys or external services."""
import argparse
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def port_available(port):
    with socket.socket() as sock:
        try:
            sock.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def check_setup(root=ROOT):
    errors = []
    python = root / 'backend/.venv/bin/python'
    if not python.is_file():
        errors.append('Backend environment missing. In backend, run: python3 -m venv .venv')
    else:
        result = subprocess.run([str(python), '-c', 'import fastapi, uvicorn, dotenv, openai, aiohttp, bs4, certifi'],
                                capture_output=True, timeout=20)
        if result.returncode:
            errors.append('Backend dependencies missing. In backend, run: .venv/bin/python -m pip install -r requirements-lock.txt')
    if not shutil.which('node') or not shutil.which('npm'):
        errors.append('Node.js and npm must be installed and available in your terminal.')
    if not (root / 'frontend/node_modules/vite/bin/vite.js').is_file():
        errors.append('Frontend dependencies missing. In frontend, run: npm ci')
    for port in (8000, 5173):
        if not port_available(port):
            errors.append(f'Port {port} is busy. If this app is already running, open http://127.0.0.1:5173. Otherwise stop its existing server with Control+C and retry.')
    return errors


def stop_children(children):
    """Stop only process groups created by this launcher, including reload workers."""
    for child in children:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for child in children:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()


def url_ready(url):
    try:
        # Local checks never use proxy environment variables.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=1) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def wait_ready(children, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(child.poll() is not None for child in children):
            raise RuntimeError('A server exited during startup. Read its error above.')
        if url_ready('http://127.0.0.1:8000/health') and url_ready('http://127.0.0.1:5173'):
            return
        time.sleep(0.2)
    raise RuntimeError('Servers did not become ready within 30 seconds. Read the logs above.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check setup without starting any servers')
    args = parser.parse_args()
    if os.name != 'posix':
        print('This launcher currently supports macOS/Linux. Use the separate backend/frontend commands on Windows.')
        return 1
    try:
        errors = check_setup()
    except (OSError, subprocess.TimeoutExpired):
        print('Could not inspect the local environment. Check your Python installation and try again.')
        return 1
    if errors:
        for error in errors:
            print(f'CHECK: {error}')
        return 1
    print('Setup ready. No API keys are needed for the free demo.', flush=True)
    if args.check:
        return 0
    children = []
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        commands = [
            ([str(ROOT / 'backend/.venv/bin/python'), '-m', 'uvicorn', 'main:app', '--reload', '--host', '127.0.0.1', '--port', '8000'], ROOT / 'backend'),
            ([shutil.which('node'), str(ROOT / 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', '5173', '--strictPort'], ROOT / 'frontend'),
        ]
        for command, cwd in commands:
            children.append(subprocess.Popen(command, cwd=cwd, start_new_session=True))
        wait_ready(children)
        print('\nApp ready: http://127.0.0.1:5173\nChoose Explore the free demo. Press Control+C here to stop both servers.\n', flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        print('A server stopped. Shutting down the other server; inspect the logs above.')
        return 1
    except KeyboardInterrupt:
        print('\nStopping both development servers…', flush=True)
        return 0
    except (OSError, RuntimeError) as exc:
        print(f'Startup failed: {exc}')
        return 1
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        stop_children(children)


if __name__ == '__main__':
    raise SystemExit(main())
