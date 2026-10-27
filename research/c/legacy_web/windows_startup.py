"""현재 사용자 로그인 자동 시작. 등록/해제는 요청한 ClaView 항목 하나만 바꾼다.

pythonw는 창을 띄우지 않는 입구일 뿐이다. 실제 실행기는 콘솔 Python을
CREATE_NO_WINDOW로 실행해 stdin/로그가 없는 pythonw 환경에서도 안정적으로 동작한다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import background
import credential_store

HERE = Path(__file__).resolve().parent
RUN_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
ENTRY = "ClaViewLocalServer-" + credential_store.PROJECT_ID


def startup_command():
    pythonw = HERE / ".venv/Scripts/pythonw.exe"
    entrypoint = HERE / "windows_startup.py"
    if not pythonw.is_file() or not entrypoint.is_file():
        raise RuntimeError("자동 시작용 Python 또는 실행 파일을 찾지 못했습니다.")
    # 경로에 공백이 없는 현재 폴더에서도 둘 다 명시적으로 인용한다.
    command = f'"{pythonw}" -X utf8 "{entrypoint}" --run'
    if len(command) > 260:
        raise RuntimeError("폴더 경로가 Windows 자동 시작 길이 제한을 넘었습니다.")
    return command


def registry():
    if os.name != "nt":
        raise RuntimeError("로그인 자동 시작은 Windows에서만 지원합니다.")
    import winreg
    return winreg


def registered_command():
    api = registry()
    try:
        with api.OpenKey(api.HKEY_CURRENT_USER, RUN_PATH) as key:
            value, kind = api.QueryValueEx(key, ENTRY)
            return value if kind == api.REG_SZ else "[unexpected registry type]"
    except FileNotFoundError:
        return None


def enable():
    command = startup_command()
    previous = registered_command()
    if previous is not None and previous != command:
        raise RuntimeError("같은 이름의 다른 자동 시작 설정이 있어 덮어쓰지 않았습니다.")
    api = registry()
    with api.CreateKeyEx(api.HKEY_CURRENT_USER, RUN_PATH, 0, api.KEY_SET_VALUE) as key:
        api.SetValueEx(key, ENTRY, 0, api.REG_SZ, command)
    if registered_command() != command:
        raise RuntimeError("자동 시작 등록을 확인하지 못했습니다.")


def disable():
    previous = registered_command()
    if previous is None:
        return False
    if previous != startup_command():
        raise RuntimeError("기존 자동 시작 값이 달라 임의로 지우지 않았습니다.")
    api = registry()
    with api.OpenKey(api.HKEY_CURRENT_USER, RUN_PATH, 0, api.KEY_SET_VALUE) as key:
        api.DeleteValue(key, ENTRY)
    return True


def enabled():
    return registered_command() == startup_command()


def configure(keys):
    """두 키를 한 자격 증명으로 저장·대조한 뒤 등록한다. 실패하면 키를 원복한다."""
    background.checked_keys(keys)
    previous = credential_store.load_keys()
    was_enabled = enabled()
    try:
        credential_store.save_keys(keys)
        if credential_store.load_keys() != keys:
            raise RuntimeError("보관한 키를 다시 읽어 확인하지 못했습니다.")
        enable()
    except Exception:
        try:
            if not was_enabled and enabled():
                disable()
        finally:
            if previous:
                credential_store.save_keys(previous)
            else:
                credential_store.delete_keys()
        raise


def run_at_logon():
    """무인 실행: 키 입력/브라우저/API 요청 없이 서버만 켠다. 실패는 별도 기록한다."""
    background.RUNTIME.mkdir(parents=True, exist_ok=True)
    logger = background.rotating_logger("startup", background.RUNTIME / "startup.log")
    secrets = []

    def event(kind, **fields):
        logger.info(json.dumps({"at": background.now(), "event": kind, **fields}, ensure_ascii=False))

    try:
        with background.exclusive_lock(background.RUNTIME / "startup.lock"):
            # 해제한 뒤 늦게 시작한 Windows 항목은 실행하지 않는다.
            if not enabled():
                event("autostart_disabled_skip")
                return 0
            keys = credential_store.load_keys()
            background.checked_keys(keys)
            secrets = list(keys.values())
            del keys
            env = dict(os.environ)
            for name in background.KEY_NAMES:
                env.pop(name, None)  # 로그인 환경에 남은 오래된 키보다 저장한 키를 사용
            command = [str(HERE / ".venv/Scripts/python.exe"), "-X", "utf8",
                       str(HERE / "launcher.py"), "--unattended", "--no-browser"]
            event("logon_start_requested")
            result = subprocess.run(command, cwd=HERE, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", timeout=240,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            logger.info(background.redact(result.stdout or "", secrets))
            event("logon_launcher_finished", exit_code=result.returncode)
            return result.returncode
    except background.AlreadyRunning:
        event("duplicate_logon_launcher_skip")
        return 0
    except Exception as exc:
        event("logon_start_failed", error_type=type(exc).__name__,
              guidance="ClaView_상태.cmd 또는 ClaView_자동시작_설정.cmd 확인")
        return 1
    finally:
        for handler in logger.handlers:
            handler.close()


if __name__ == "__main__":
    raise SystemExit(run_at_logon() if sys.argv[1:] == ["--run"] else 2)
