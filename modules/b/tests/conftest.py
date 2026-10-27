"""Block common Python TCP entry points in the default test suite."""

import socket
from datetime import date

import pytest

from claview_b import contracts

# Config().as_of defaults to the query date. The suite pins that "today" so period expectations
# built around 2026-10-01 stay reproducible; tests that need another date pass `as_of`.
PINNED_QUERY_DATE = date(2026, 10, 1)


@pytest.fixture(scope="session", autouse=True)
def pin_query_date():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(contracts, "_today", lambda: PINNED_QUERY_DATE)
    yield
    monkeypatch.undo()


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """Prevent accidental API calls; this is not an OS-level network sandbox."""

    def blocked(*args, **kwargs):
        raise AssertionError("Network access is forbidden in offline tests.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
