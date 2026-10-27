"""Check the base environment without credentials or optional experiments."""

import importlib.metadata
import socket
import sys
from pathlib import Path

import pytest

import claview_b


def test_python_version():
    assert sys.version_info[:2] == (3, 12)


def test_package_import():
    assert claview_b.__name__ == "claview_b"


def test_installed_packages_match_requirements():
    """The shared requirements.txt (one file for every service) is what is installed."""
    requirements = Path(__file__).resolve().parents[3] / "requirements.txt"
    pins = [line.split("#")[0].strip() for line in requirements.read_text(encoding="utf-8").splitlines()]
    for pin in filter(None, pins):
        name, version = pin.split("==")
        assert importlib.metadata.version(name.split("[")[0]) == version, pin


@pytest.mark.parametrize("method", ["connect", "connect_ex", "create_connection"])
def test_network_guard(method):
    address = ("127.0.0.1", 9)
    with pytest.raises(AssertionError, match="Network access is forbidden"):
        if method == "create_connection":
            socket.create_connection(address)
        else:
            with socket.socket() as connection:
                getattr(connection, method)(address)
