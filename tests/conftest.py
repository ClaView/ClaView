"""Block common Python TCP entry points in the default test suite."""

import socket

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Prevent accidental API calls; this is not an OS-level network sandbox."""

    def blocked(*args, **kwargs):
        raise AssertionError("Network access is forbidden in offline tests.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
