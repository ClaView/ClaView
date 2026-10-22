"""Run a command in the repo, write a log with command, times, code-state hashes and output."""

import datetime as dt
import hashlib
import subprocess
import sys
from pathlib import Path

repo = Path(sys.argv[1])
log = Path(sys.argv[2])
cmd = sys.argv[3:]

KEY_FILES = [
    "packages/claview-period/src/claview_period/resolver.py",
    "packages/claview-period/src/claview_period/contract.py",
    "packages/claview-period/src/claview_period/policy.py",
    "packages/claview-period/src/claview_period/period_policy.json",
    "packages/claview-period/pyproject.toml",
    "src/claview/b/period_resolver.py",
    "src/claview/b/contract.py",
    "src/claview/b/run.py",
    "src/claview/b/adapter_v3.py",
    "src/claview/stages.py",
    "tests/b_v2/test_stage3.py",
    "tests/b_v2/test_period_resolver.py",
    "packages/claview-period/tests/test_claview_period.py",
    "pyproject.toml",
    "uv.lock",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(*roots: str) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    for root in roots:
        for p in sorted((repo / root).rglob("*")):
            if not p.is_file() or "__pycache__" in p.parts or p.suffix == ".pyc":
                continue
            rel = p.relative_to(repo).as_posix()
            h.update(rel.encode() + b"\0" + sha(p).encode() + b"\n")
            n += 1
    return h.hexdigest(), n


start = dt.datetime.now().astimezone()
proc = subprocess.run(
    cmd, cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace"
)
end = dt.datetime.now().astimezone()
tree, count = tree_hash(
    "src", "packages/claview-period/src", "tests", "packages/claview-period/tests"
)
lines = [
    f"command: {' '.join(cmd)}",
    "cwd: <repo root>",
    f"start: {start.isoformat(timespec='seconds')}",
    f"end: {end.isoformat(timespec='seconds')}",
    f"exit_code: {proc.returncode}",
    f"code_tree_sha256 (src, tests, package src+tests; {count} files): {tree}",
    "key files sha256[:12]:",
    *[f"  {f}: {sha(repo / f)[:12]}" for f in KEY_FILES],
    "=" * 70,
    proc.stdout,
    proc.stderr,
]
log.parent.mkdir(parents=True, exist_ok=True)
log.write_text("\n".join(lines), encoding="utf-8")
print(log, "exit", proc.returncode)
print(proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-500:])
