"""Fingerprint and copy the exact local source a live validation run uses.

Offline only. Includes untracked source and test files (a plain `git diff HEAD` misses them),
never secrets or generated artifacts. File contents are hashed, never printed.
"""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Everything that can change what a validation run does.
INCLUDED_PREFIXES = ('backend/', 'frontend/src/', 'frontend/index.html', 'frontend/package.json',
                     'frontend/package-lock.json', 'scripts/', '.github/')
EXCLUDED_PARTS = {'.venv', 'venv', 'env', 'node_modules', 'dist', '__pycache__', '.pytest_cache', 'results'}
SECRET_SUFFIXES = ('.pem', '.key')


def is_secret(path: str) -> bool:
    name = Path(path).name
    return (name.startswith('.env') and name != '.env.example') or name.endswith(SECRET_SUFFIXES)


def _git(root: Path, *args: str) -> str:
    # Optional locks off: inspecting status must never leave an index.lock behind.
    env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, env=env)
    if result.returncode:
        raise RuntimeError(f'git {args[0]} failed; a git checkout is required for validation snapshots.')
    return result.stdout.decode('utf-8', 'surrogateescape')


def source_files(root: Path = ROOT) -> list[str]:
    listed = _git(root, 'ls-files', '--cached', '--others', '--exclude-standard', '-z').split('\0')
    return sorted({path for path in listed
                   if path.startswith(INCLUDED_PREFIXES)
                   and not is_secret(path)
                   and not EXCLUDED_PARTS & set(Path(path).parts)
                   and (root / path).is_file()})


def manifest(root: Path = ROOT) -> dict:
    files = {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in source_files(root)}
    fingerprint = hashlib.sha256(''.join(f'{path}\0{digest}\n' for path, digest in files.items()).encode()).hexdigest()
    try:
        head = _git(root, 'rev-parse', 'HEAD').strip()
    except RuntimeError:
        head = None  # A repository without commits can still be fingerprinted.
    status = _git(root, 'status', '--porcelain', '--untracked-files=all', '-z').split('\0')
    changed = sorted({entry[3:] for entry in status if len(entry) > 3 and entry[3:] in files})
    untracked = sorted({entry[3:] for entry in status if entry.startswith('?? ') and entry[3:] in files})
    return {'fingerprint': fingerprint, 'git_head': head, 'file_count': len(files),
            'uncommitted_files': changed, 'untracked_files': untracked, 'files': files}


def capture(results_dir: Path, root: Path = ROOT) -> dict:
    """Copy the fingerprinted files once per fingerprint and return the manifest."""
    info = manifest(root)
    target = results_dir / 'snapshots' / info['fingerprint'][:16]
    if not (target / 'manifest.json').exists():
        staging = target.with_name(target.name + '.tmp')
        shutil.rmtree(staging, ignore_errors=True)
        for path in info['files']:
            destination = staging / 'files' / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / path, destination)
        (staging / 'manifest.json').write_text(json.dumps(info, indent=2))
        shutil.rmtree(target, ignore_errors=True)  # Only an interrupted copy without a manifest can be here.
        staging.replace(target)
    stored = json.loads((target / 'manifest.json').read_text())
    if stored['fingerprint'] != info['fingerprint']:
        raise RuntimeError('Existing snapshot does not match the current code; refusing to reuse it.')
    return dict(info, snapshot_dir=str(target))
