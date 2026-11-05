"""Run isolated offline Python suites and write logs under a new runs/checks directory."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
SUITES = {"common": "common/tests", "a": "modules/a/tests", "a-tl": "modules/a/tests_tl",
          "a-api": "modules/a/tests_api", "b": "modules/b/tests", "b-api": "modules/b/tests_api",
          "c": "modules/c/tests", "pipeline": "pipeline/tests", "pipeline-api": "pipeline/tests_api"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", action="append", choices=list(SUITES))
    args = parser.parse_args()
    target = ROOT / "runs" / "checks" / uuid4().hex
    target.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items()
           if not any(word in k.upper() for word in ("KEY", "TOKEN", "SECRET"))}
    paths = ("common", "modules/a", "modules/b", "modules/c", "pipeline")
    env.update(PYTHONPATH=os.pathsep.join(str(ROOT / p) for p in paths),
               PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", CLAVIEW_MODE="synthetic")
    results = []
    for suite in args.suite or SUITES:
        log = target / f"{suite}.txt"
        with log.open("w", encoding="utf-8") as stream:
            proc = subprocess.run([sys.executable, "-B", "-X", "utf8", "-m", "pytest", SUITES[suite],
                                   "-q", "-p", "no:cacheprovider", "--tb=short", "--basetemp",
                                   str(target / f"temp-{suite}")], cwd=ROOT, env=env, stdout=stream,
                                  stderr=subprocess.STDOUT)
        lines = log.read_text(encoding="utf-8").splitlines()
        summary = next((line for line in reversed(lines) if " in " in line and
                        any(word in line for word in ("passed", "failed", "error", "skipped"))), "No test summary")
        results.append({"suite": suite, "exit_code": proc.returncode, "summary": summary})
        print(f"{suite}: {summary}", flush=True)
    (target / "summary.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Logs: {target.relative_to(ROOT)}", flush=True)
    return int(any(row["exit_code"] for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
