"""고정된 공유본을 읽기 전용으로 사용한다. 서로 다른 A/B 모듈을 섞어 import하지 않는다."""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[1]
A_ROOT = WORKSPACE / "vendor/a"
B_ROOT = WORKSPACE / "vendor/b"
C_ROOT = HERE.parent / "bc_demo"
UI_ROOT = HERE.parent / "ui_a_highcharts"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def configure():
    # 외부 추적 서비스로 질문/응답을 보내지 않는다.
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGSMITH_TRACING"] = "false"
    for root in (A_ROOT / "src", B_ROOT / "src", C_ROOT):
        if not root.is_dir():
            raise RuntimeError(f"공유본 폴더를 찾을 수 없습니다: {root.name}")
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
    import claview_v3.paths
    import claview.fetch
    import c_renderer
    expected = ((claview_v3.paths, A_ROOT / "src/claview_v3/paths.py"),
                (claview.fetch, B_ROOT / "src/claview/fetch.py"),
                (c_renderer, C_ROOT / "c_renderer.py"))
    if any(Path(module.__file__).resolve() != path.resolve() for module, path in expected):
        raise RuntimeError("다른 버전의 모듈이 로드되었습니다. 새 터미널에서 실행하세요.")
    # 같은 이름의 표라도 다른 메타 판본을 읽으면 연결을 중단한다.
    pairs = [(A_ROOT / "reference/baseline-1006/data" / name, B_ROOT / "data" / name)
             for name in ("catalog/search_catalog.json", "raw/boK_meta_raw.jsonl")]
    if any(sha(a) != sha(b) for a, b in pairs):
        raise RuntimeError("A와 B의 메타정보 판본이 다릅니다.")
    return {"A": "20261020 rules + selection", "B": "20261016 compact",
            "metadata_sha256": sha(pairs[0][1]), "raw_metadata_sha256": sha(pairs[1][1])}
