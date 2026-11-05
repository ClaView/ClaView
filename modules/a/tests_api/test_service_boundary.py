"""Only the current AHTTP routes are installed in the service image."""

import importlib.util

from fastapi.testclient import TestClient

from claview_a_api.api import app


def test_service_has_only_current_a_routes():
    paths = {route.path for route in app.routes if route.path.startswith("/a/")}
    assert paths == {"/a/select", "/a/answer", "/a/records/{session_id}"}
    assert importlib.util.find_spec("claview_a_legacy") is None
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok", "service": "module-a"}
