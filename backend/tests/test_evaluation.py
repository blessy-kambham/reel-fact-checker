"""Run the versioned regression corpus without permitting network access."""
import asyncio
import socket
import pytest
from evaluation.run import load_cases, evaluate_case

@pytest.mark.parametrize('case', load_cases(), ids=lambda case: case['id'])
def test_policy_case(case, monkeypatch):
    def reject_network(*args, **kwargs):
        raise AssertionError('Offline evaluation attempted to use the network')
    monkeypatch.setattr(socket.socket, 'connect', reject_network)
    monkeypatch.setattr(socket, 'getaddrinfo', reject_network)
    result = asyncio.run(evaluate_case(case))
    assert result['passed'], result
