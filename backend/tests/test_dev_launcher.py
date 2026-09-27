"""Startup errors should be actionable, and existing services must be left alone."""
import importlib.util
from pathlib import Path
import socket
import pytest

spec = importlib.util.spec_from_file_location('dev_launcher', Path(__file__).resolve().parents[2] / 'scripts/dev.py')
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)


def test_missing_dependencies_have_setup_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(dev.shutil, 'which', lambda name: None)
    monkeypatch.setattr(dev, 'port_available', lambda port: True)
    errors = dev.check_setup(tmp_path)
    assert any('python3 -m venv' in error for error in errors)
    assert any('npm ci' in error for error in errors)
    assert any('Node.js' in error for error in errors)


def test_busy_port_is_detected_without_stopping_listener():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert dev.port_available(port) is False
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            pass


def test_exited_server_fails_readiness_immediately():
    class Exited:
        def poll(self):
            return 1
    with pytest.raises(RuntimeError, match='exited during startup'):
        dev.wait_ready([Exited()])


def test_startup_timeout_is_explicit(monkeypatch):
    class Running:
        def poll(self):
            return None
    monkeypatch.setattr(dev, 'url_ready', lambda url: False)
    with pytest.raises(RuntimeError, match='did not become ready'):
        dev.wait_ready([Running()], timeout=0)
