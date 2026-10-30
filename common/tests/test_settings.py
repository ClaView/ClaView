"""Shared settings: paths in one place, keys never shown, malformed keys count as not set."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claview_common import settings


def test_paths_point_at_the_repository_files():
    paths = settings.PATHS
    assert paths.catalog_file.is_file() and paths.meta_raw_file.is_file() and paths.golden_file.is_file()
    assert paths.catalog_file.parent.parent == paths.data and paths.data.parent == paths.root


def test_project_directory_overrides_root(tmp_path):
    cfg = settings.load({"PROJECT_MAIN_DIRECTORY": str(tmp_path)})
    assert cfg.paths.root == tmp_path and cfg.paths.meta_raw_file == tmp_path / "data" / "raw" / "boK_meta_raw.jsonl"


def test_keys_are_wrapped_and_never_shown():
    cfg = settings.load({"OPENAI_API_KEY": " sk-secret-value ", "KOSIS_API_KEY": "kosis-secret"})
    assert settings.reveal(cfg.openai_key) == "sk-secret-value" and settings.reveal(cfg.kosis_key) == "kosis-secret"
    shown = " ".join([repr(cfg), str(cfg), f"{cfg.openai_key}", json.dumps(cfg.public())])
    assert "secret" not in shown.replace("Secret", "")
    assert cfg.public() == {"mode": "synthetic", "openai_key_set": True, "kosis_key_set": True}


def test_the_hcx_key_is_not_read():
    cfg = settings.load({"CLOVASTUDIO_API_KEY": "nv-secret-value"})
    assert cfg.openai_key is None and "hcx" not in json.dumps(cfg.public()) and "nv-secret" not in repr(cfg)


def test_the_b_model_id_is_fixed():
    assert settings.B_MODEL_ID == "gpt-6.1-terra"


@pytest.mark.parametrize("value", ["", "   ", "a b", "key\x00", "k\ney", None, 123])
def test_malformed_keys_count_as_not_set(value):
    assert settings.usable_key(value) is None
    env = {} if value is None else {"KOSIS_API_KEY": value} if isinstance(value, str) else {}
    assert settings.load(env).kosis_key is None


def test_mode_is_checked():
    assert settings.load({"CLAVIEW_MODE": "LIVE"}).live
    with pytest.raises(ValueError):
        settings.load({"CLAVIEW_MODE": "prod"})


def test_only_settings_reads_key_variables():
    """Key environment variables are read in common/claview_common/settings.py only (B's own CLI excepted)."""
    root = settings.PATHS.root
    allowed = {root / "common" / "claview_common" / "settings.py", root / "modules" / "b" / "claview_b" / "cli.py"}
    names = tuple(settings.KEY_ENVS.values())
    offenders = []
    folders = (
        "common/claview_common",
        "pipeline/claview_pipeline",
        "modules/a/claview_a_api",
        "modules/b/claview_b_api",
        "modules/c",
    )
    for folder in folders:
        for path in Path(root, folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if path not in allowed and "environ" in text and any(n in text for n in names):
                offenders.append(str(path.relative_to(root)))
    assert offenders == []
