"""Check the shared environment without API keys or optional experiments."""

import importlib.metadata
import sys

import claview


def test_python_version():
    assert sys.version_info[:2] == (3, 12)


def test_package_import():
    assert claview.__name__ == "claview"


def test_package_installation():
    assert importlib.metadata.version("claview") == "0.1.0"
