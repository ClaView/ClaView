"""Test helpers shared by the API suites.

`block_external_network` refuses every socket connection that is not loopback. The API suites
need loopback (FastAPI's TestClient runs an event loop, which on Windows builds its self-pipe
with a local socket pair); anything else is a test bug and fails loudly.
"""

from __future__ import annotations

import ipaddress
import socket

import pytest

_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_GETADDRINFO = socket.getaddrinfo
LOCAL_NAMES = {"localhost", "testserver"}


def _is_loopback(host) -> bool:
    if isinstance(host, bytes):
        host = host.decode()
    if host in LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _refuse(host):
    raise AssertionError(f"Network access is forbidden in offline tests (host {host!r}).")


def install(monkeypatch: pytest.MonkeyPatch) -> None:
    def connect(self, address):
        if isinstance(address, tuple) and not _is_loopback(address[0]):
            _refuse(address[0])
        return _REAL_CONNECT(self, address)

    def connect_ex(self, address):
        if isinstance(address, tuple) and not _is_loopback(address[0]):
            _refuse(address[0])
        return _REAL_CONNECT_EX(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if host is not None and not _is_loopback(host):
            _refuse(host)
        return _REAL_GETADDRINFO(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch):
    install(monkeypatch)
    yield
