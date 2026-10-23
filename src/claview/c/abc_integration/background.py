"""로컬 서버 관리자. 창과 분리되어 자기 자식 서버만 재실행한다.

API 키는 최초 표준입력 → 관리자 메모리 → 자식 환경변수로만 전달한다.
정상 중지는 이번 실행의 stop 요청 파일로 구분한다. PID만 보고 다른 프로세스를
종료하지 않으며, 질문을 다시 제출하거나 API 요청을 재생하지 않는다.
"""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
import http.client
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
from uuid import uuid4

HERE = Path(__file__).resolve().parent
RUNTIME = HERE / ".runtime"
KEY_NAMES = ("CLOVASTUDIO_API_KEY", "KOSIS_API_KEY")
KST = timezone(timedelta(hours=9))


def now():
    return datetime.now(KST).isoformat(timespec="seconds")


class AlreadyRunning(RuntimeError):
    pass


@contextlib.contextmanager
def exclusive_lock(path):
    """파일 내용/PID가 아니라 OS 잠금으로 중복 실행을 막는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0"); handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AlreadyRunning("같은 작업이 이미 실행 중입니다.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def monitor_is_running(root=RUNTIME):
    try:
        with exclusive_lock(root / "monitor.lock"):
            return False
    except AlreadyRunning:
        return True


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows에서 상태를 읽는 순간의 짧은 공유 잠금은 관리자를 종료시킬 이유가 아니다.
    try:
        for attempt in range(6):
            try:
                os.replace(temp, path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(.02 * (attempt + 1))
    finally:
        if temp.exists():
            temp.unlink()


def read_status(root=RUNTIME):
    try:
        data = json.loads((root / "status.json").read_text(encoding="utf-8"))
        if isinstance(data, dict) and re.fullmatch(r"[a-f0-9]{32}", data.get("instance_id", "")):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {}


def stop_path(instance_id, root=RUNTIME):
    if not isinstance(instance_id, str) or not re.fullmatch(r"[a-f0-9]{32}", instance_id):
        raise ValueError("올바르지 않은 실행 번호")
    return root / ("stop-" + instance_id + ".json")


def request_stop(root=RUNTIME):
    """중지 명령은 현재 잠금을 가진 실행에만 전달한다. 오래된 PID는 사용하지 않는다."""
    if not monitor_is_running(root):
        return False
    status = read_status(root)
    if not status:
        raise RuntimeError("관리자가 시작 중입니다. 잠시 후 중지를 다시 실행해 주세요.")
    atomic_json(stop_path(status["instance_id"], root),
                {"instance_id": status["instance_id"], "requested_at": now()})
    return True


def stop_requested(instance_id, root):
    try:
        return json.loads(stop_path(instance_id, root).read_text(encoding="utf-8")).get("instance_id") == instance_id
    except (OSError, ValueError, AttributeError):
        return False


def checked_keys(values):
    if not isinstance(values, dict) or set(values) != set(KEY_NAMES):
        raise ValueError("키 전달 형식 오류")
    if any(not isinstance(v, str) or not 1 <= len(v) <= 2048 or any(c in v for c in "\r\n\0") for v in values.values()):
        raise ValueError("키가 비어 있거나 형식이 올바르지 않습니다.")
    return values


def redact(text, secrets):
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    return re.sub(r"(?i)(Bearer\s+)[^\s\"']+", r"\1[REDACTED]", text)


def rotating_logger(name, path):
    logger = logging.Logger(name, logging.INFO)
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def local_info(port=8775):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        connection.request("GET", "/api/info")
        response = connection.getresponse()
        raw = response.read(65537)
        if response.status != 200 or len(raw) > 65536:
            return None
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("mode") == "actual_pipeline":
            return {k: data.get(k) for k in ("mode", "hcx_ready", "kosis_ready", "c_analysis_version", "server_pid", "busy")}
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def detached_options():
    if os.name == "nt":
        # 부모 콘솔의 X/Ctrl+C와 분리한다. 작업 스케줄러·시작프로그램은 등록하지 않는다.
        return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def start_background(keys, root=RUNTIME):
    checked_keys(keys)
    root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for name in KEY_NAMES:
        env.pop(name, None)
    # 키는 명령 인자/디스크에 쓰지 않는다. 표준입력 파이프는 전송 직후 닫는다.
    process = subprocess.Popen([sys.executable, "-X", "utf8", "-u", str(HERE / "background.py")],
        cwd=HERE, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", env=env, close_fds=True, **detached_options())
    payload = json.dumps({"instance_id": uuid4().hex, "keys": keys})
    try:
        process.stdin.write(payload + "\n"); process.stdin.flush()
    finally:
        process.stdin.close()
    return process


def retry_delay(failures):
    return min(60, 2 ** min(failures, 6))


def supervise(instance_id, keys, root=RUNTIME):
    checked_keys(keys)
    stop_path(instance_id, root)  # 실행 번호 검증
    root.mkdir(parents=True, exist_ok=True)
    events = rotating_logger("lifecycle", root / "lifecycle.jsonl")
    output = rotating_logger("server", root / "server.log")
    state = {"instance_id": instance_id, "monitor_pid": os.getpid(), "server_pid": None,
             "started_at": now(), "state": "starting", "restarts": 0, "url": "http://127.0.0.1:8775/"}
    child = None

    def event(kind, **fields):
        events.info(json.dumps({"at": now(), "event": kind, "instance_id": instance_id, **fields}, ensure_ascii=False))

    def status(**fields):
        state.update(fields, updated_at=now())
        atomic_json(root / "status.json", state)

    def drain(process):
        for line in process.stdout:
            output.info(now() + " " + redact(line.rstrip("\r\n"), keys.values()))
        process.stdout.close()

    def halt_owned(process, stop_file):
        # 가능하면 서버의 shutdown()을 거친다. 응답이 없을 때만 자기 자식 Popen을 종료한다.
        atomic_json(stop_file, {"reason": "user_requested", "at": now()})
        try:
            return process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            event("forced_stop", server_pid=process.pid, reason="graceful_stop_timeout")
            if os.name == "nt" and process.poll() is None:
                # venv의 python.exe는 실제 Python 자식을 띄울 수 있다. 자기 Popen의 트리만 정리한다.
                killer = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/taskkill.exe"
                subprocess.run([str(killer), "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                process.terminate()
            return process.wait(timeout=8)

    try:
        with exclusive_lock(root / "monitor.lock"):
            status()
            event("monitor_started", pid=os.getpid(), credential_storage="memory_only")
            failures = 0
            while not stop_requested(instance_id, root):
                # 다른 프로그램의 포트를 빼앗거나 종료하지 않는다.
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    try:
                        probe.bind(("127.0.0.1", 8775))
                    except OSError:
                        status(state="blocked_port", server_pid=None)
                        event("port_in_use", reason="another_listener_not_replaced")
                        break
                generation = uuid4().hex
                child_stop = root / ("child-stop-" + generation + ".json")
                env = {**os.environ, **keys, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}
                started = time.monotonic()
                ready = False
                try:
                    child = subprocess.Popen([sys.executable, "-X", "utf8", "-u", str(HERE / "server.py"),
                        "--supervised-stop-file", str(child_stop)], cwd=HERE, env=env,
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace", close_fds=True,
                        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
                except OSError as exc:
                    event("server_spawn_failed", error_type=type(exc).__name__, os_error=exc.errno)
                    code = None
                else:
                    reader = threading.Thread(target=drain, args=(child,), daemon=True); reader.start()
                    status(state="starting", server_pid=child.pid, ready_at=None)
                    event("server_started", server_pid=child.pid, restart_count=state["restarts"])
                    while child.poll() is None and not stop_requested(instance_id, root):
                        info = local_info()
                        if info and not ready:
                            ready = True
                            event("server_ready", server_pid=child.pid)
                            status(state="running", ready_at=now(), worker_pid=info.get("server_pid"))
                        else:
                            status()
                        time.sleep(1)
                    if stop_requested(instance_id, root):
                        event("stop_requested", reason="explicit_stop_command")
                        status(state="stopping")
                        code = halt_owned(child, child_stop)
                        reader.join(timeout=3)
                        event("server_exited", exit_code=code, intentional=True)
                        status(state="stopped", server_pid=None, exit_code=code, stopped_at=now())
                        child = None
                        break
                    code = child.wait(); reader.join(timeout=3); child = None
                # 0으로 끝나도 명시적 중지가 아니면 예상치 못한 종료로 보고 재시작한다.
                if time.monotonic() - started > 120:
                    failures = 0
                failures += 1
                delay = retry_delay(failures)
                state["restarts"] += 1
                event("server_exited", exit_code=code, intentional=False,
                      reason="unexpected_exit_cause_unknown", next_retry_seconds=delay)
                status(state="restarting", server_pid=None, exit_code=code, retry_seconds=delay,
                       interrupted_requests="다시 제출하지 않음. 진행 중 질문은 화면에서 확인 후 재실행 필요.")
                deadline = time.monotonic() + delay
                while time.monotonic() < deadline and not stop_requested(instance_id, root):
                    time.sleep(.25)
            if state["state"] not in {"stopped", "blocked_port"}:
                event("stop_requested", reason="explicit_stop_command")
                status(state="stopped", server_pid=None, stopped_at=now())
            event("monitor_stopped", reason=state["state"])
    except AlreadyRunning:
        event("duplicate_monitor_ignored")
    except Exception as exc:
        # 자식의 원시 예외는 위 drain에서 키를 가린다. 관리자는 비밀이 섞일 수 있는 repr를 쓰지 않는다.
        event("monitor_failed", error_type=type(exc).__name__)
        if child is not None and child.poll() is None:
            halt_owned(child, child_stop)
        status(state="failed", server_pid=None, stopped_at=now(), error_type=type(exc).__name__)
        raise
    finally:
        for logger in (events, output):
            for handler in logger.handlers:
                handler.close()


def main():
    try:
        raw = sys.stdin.buffer.readline(12000)
        sys.stdin.close()
        payload = json.loads(raw)
        keys = checked_keys(payload["keys"])
        instance_id = payload["instance_id"]
        del raw, payload
        supervise(instance_id, keys)
        return 0
    except Exception:
        # 표준입력 bootstrap/키가 traceback·오류 출력에 노출되지 않게 한다.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
