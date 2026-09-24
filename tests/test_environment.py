"""Check the base environment without credentials or optional experiments."""

import importlib.metadata
import socket
import sys
import tomllib
from pathlib import Path

import pytest

import claview


def test_python_version():
    assert sys.version_info[:2] == (3, 12)


def test_package_import():
    assert claview.__name__ == "claview"


def test_package_installation_matches_project():
    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with project_file.open("rb") as source:
        project = tomllib.load(source)["project"]
    assert importlib.metadata.version(project["name"]) == project["version"]


@pytest.mark.parametrize("method", ["connect", "connect_ex", "create_connection"])
def test_network_guard(method):
    address = ("127.0.0.1", 9)
    with pytest.raises(AssertionError, match="Network access is forbidden"):
        if method == "create_connection":
            socket.create_connection(address)
        else:
            with socket.socket() as connection:
                getattr(connection, method)(address)
