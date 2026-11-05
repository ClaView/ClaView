"""modules/a/tests_api: A's HTTP layer (claview_a_api). Its own pytest process: modules/a/tests_tl installs
A's import guard, which refuses claview_common and the loopback socket pair FastAPI's TestClient needs."""

from claview_common.testing import block_external_network  # noqa: F401  (autouse fixture)
