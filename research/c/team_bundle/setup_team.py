"""Install a separate environment; never copy the author's environment or keys."""
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent
if sys.version_info[:2] != (3, 12):
    raise SystemExit("Python 3.12가 필요합니다. Python 3.12 설치 후 다시 실행하세요.")
python = ROOT / ".venv/Scripts/python.exe"
if not python.exists():
    print("전용 실행 환경을 만듭니다.", flush=True)
    venv.EnvBuilder(with_pip=True).create(ROOT / ".venv")
subprocess.run([str(python), "-m", "pip", "install", "--index-url", "https://pypi.org/simple", "--retries", "1", "--timeout", "20", "-r", str(ROOT / "requirements.txt")], check=True)
subprocess.run([str(python), "-X", "utf8", str(ROOT / "team_check.py")], cwd=ROOT, check=True)
print("설치 완료! 02_start.cmd를 더블클릭하세요.")
