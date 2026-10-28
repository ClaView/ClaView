"""Specification cases: an expected failure counts only if it is a stretch-2 stub's NotImplementedError.

The behaviour tests are ``xfail(strict=True, raises=NotImplementedError)``. This hook also requires the message of a
stub ("stretch 2: <node>"), so a NotImplementedError raised anywhere else (a library, a forgotten abstract method)
is reported as a failure instead of an expected one.
"""

from __future__ import annotations

import pytest

STUB_PREFIX = "stretch 2: "


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if call.when != "call" or call.excinfo is None or not call.excinfo.errisinstance(NotImplementedError):
        return
    if not str(call.excinfo.value).startswith(STUB_PREFIX):
        report.outcome = "failed"
        report.longrepr = f"NotImplementedError not raised by a stretch-2 stub: {call.excinfo.value!r}"
        if hasattr(report, "wasxfail"):
            del report.wasxfail
