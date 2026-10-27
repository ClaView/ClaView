"""더블클릭 실행기. 준비 후 종료해도 백그라운드 관리자가 서버를 유지한다.

키를 소스·명령 인자·평문 파일에 적지 않는다. 요청한 경우 Windows 자격 증명
관리자의 현재 프로젝트 항목에서 읽는다. 시작 확인은 로컬 /api/info만 읽으며,
질문 제출이나 HCX·KOSIS 호출은 하지 않는다.
"""
from __future__ import annotations

import argparse
import errno
import getpass
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import webbrowser

import background
import credential_store
import windows_startup

HERE = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8775/"
MAX_INFO_BYTES = 65536
BROWSER_ENABLED = True
UNATTENDED = False


def port_is_available():
    """꺼진 서버의 연결 거부를 오래 기다리지 않고 주소 사용 여부를 확인한다."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        # Windows의 포트 공유 때문에 기존 서버를 빈 주소로 오인하지 않도록 한다.
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", 8775))
        except OSError as exc:
            if exc.errno in (errno.EADDRINUSE, errno.EACCES) or getattr(exc, "winerror", None) in (10048, 10013):
                return False
            raise RuntimeError("로컬 서버 주소를 준비하지 못했습니다. 네트워크 권한을 확인해 주세요.") from exc
    return True


def server_is_running():
    """다른 프로그램이 8775번을 사용 중이면 덮어쓰거나 종료하지 않는다."""
    if port_is_available():
        return False
    connection = http.client.HTTPConnection("127.0.0.1", 8775, timeout=1)
    try:
        connection.request("GET", "/api/info")
        response = connection.getresponse()
        raw = response.read(MAX_INFO_BYTES + 1)
        if response.status != 200 or len(raw) > MAX_INFO_BYTES:
            raise ValueError("unexpected response")
        info = json.loads(raw)
        if not isinstance(info, dict) or info.get("mode") != "actual_pipeline":
            raise ValueError("not a ClaView server")
        if not all(isinstance(info.get(key), bool) for key in ("hcx_ready", "kosis_ready")):
            raise ValueError("unexpected readiness data")
        return True
    except OSError as exc:
        if exc.errno == errno.ECONNREFUSED or getattr(exc, "winerror", None) == 10061:
            return False
        raise RuntimeError("8775번 주소의 응답을 확인하지 못했습니다. 잠시 후 다시 실행해 주세요.") from exc
    except (ValueError, http.client.HTTPException) as exc:
        raise RuntimeError("8775번을 다른 프로그램이 사용 중입니다. 해당 프로그램을 확인해 주세요.") from exc
    finally:
        connection.close()


def open_page():
    """기본 브라우저를 연다. 자동 열기가 안 돼도 사용자가 주소를 알 수 있다."""
    print(f"화면 주소: {URL}", flush=True)
    if not BROWSER_ENABLED:
        return
    try:
        opened = webbrowser.open(URL, new=2)
    except (OSError, webbrowser.Error):
        opened = False
    if not opened:
        print("자동으로 열리지 않으면 위 주소를 크롬·엣지 주소창에 붙여넣어 주세요.", flush=True)


def open_when_ready(process, stopped):
    """키 입력·메타정보 준비가 끝나기 전에 빈 브라우저를 열지 않는다."""
    while not stopped.wait(0.5):
        if process.poll() is not None:
            return
        try:
            ready = server_is_running()
        except RuntimeError:
            # 로딩 중의 순간적인 응답 실패는 기다린다. 실제 시작 오류는 자식이 출력한다.
            continue
        if ready:
            open_page()
            return


def build_screen():
    # 기존 run.ps1과 같은 생성기를 재사용한다. A/B 원본이나 실제 조회 자료는 바꾸지 않는다.
    subprocess.run([sys.executable, "-X", "utf8", "ui_build.py"], cwd=HERE, check=True)


def run_foreground():
    # 명령 인자에는 키가 없다. 자식 서버가 같은 창에서 숨김 입력을 받는다.
    process = subprocess.Popen(
        [sys.executable, "-X", "utf8", "server.py", "--prompt-keys"], cwd=HERE,
    )
    stopped = threading.Event()
    watcher = threading.Thread(target=open_when_ready, args=(process, stopped), daemon=True)
    watcher.start()
    try:
        return process.wait()
    except KeyboardInterrupt:
        print("\n서버를 종료합니다.", flush=True)
        # Ctrl+C는 같은 콘솔의 자식 서버에도 전달된다. 먼저 정상 종료를 기다린다.
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # 이번 실행기가 직접 시작한 자식만 정리한다. 기존 다른 서버는 건드리지 않는다.
            process.terminate()
            process.wait(timeout=5)
        return 0
    finally:
        stopped.set()
        watcher.join(timeout=2)


def obtain_keys(interactive=True):
    """환경변수 → 이 프로젝트의 Windows 보관 키 → 숨김 입력 순서.

로그인 자동 실행은 절대 입력을 기다리지 않는다. 없는 키를 파일에서 임의로
찾거나, 브라우저에 보내거나, 키가 없는 상태로 서버를 시작하지 않는다.
"""
    keys = {name: os.environ.get(name, "").strip() for name in background.KEY_NAMES}
    if not all(keys.values()):
        saved = credential_store.load_keys() if os.name == "nt" else {}
        keys = {name: keys[name] or saved.get(name, "") for name in background.KEY_NAMES}
    for name, title in zip(background.KEY_NAMES, ("HCX", "KOSIS")):
        if not keys[name]:
            if not interactive:
                raise RuntimeError("보관된 API 키가 없습니다. ClaView_자동시작_설정.cmd를 실행해 주세요.")
            keys[name] = getpass.getpass(f"{title} key (hidden): ").strip()
    return background.checked_keys(keys)


def setup_autostart():
    keys = obtain_keys()
    windows_startup.configure(keys)
    del keys
    print("설정 완료: 현재 Windows 사용자로 로그인하면 서버가 창 없이 자동 시작됩니다.", flush=True)
    print("키는 Windows 자격 증명 관리자에 보관했습니다. 평문 파일/HTML에는 적지 않았습니다.", flush=True)
    print("브라우저 주소: " + URL, flush=True)
    print("해제: ClaView_자동시작_해제.cmd / 키 삭제: ClaView_키삭제.cmd", flush=True)
    return 0


def run_server():
    """키를 보안 저장소/숨김 입력에서 읽어 관리자에게 파이프로 한 번만 전달한다."""
    keys = obtain_keys(interactive=not UNATTENDED)
    process = background.start_background(keys)
    del keys
    print("백그라운드 관리자를 시작했습니다. 준비가 끝나면 이 실행 창은 닫아도 됩니다.", flush=True)
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        if server_is_running():
            print("서버 준비 완료 · 창을 닫아도 서버가 유지됩니다.", flush=True)
            print("중지: ClaView_중지.cmd / 상태·기록: ClaView_상태.cmd", flush=True)
            open_page()
            return 0
        if process.poll() is not None and not background.monitor_is_running():
            raise RuntimeError("관리자를 시작하지 못했습니다. .runtime/lifecycle.jsonl을 확인해 주세요.")
        time.sleep(.5)
    raise RuntimeError("서버가 아직 준비되지 않았습니다. ClaView_상태.cmd에서 상태와 오류를 확인해 주세요.")


def show_status():
    running = background.monitor_is_running()
    state = background.read_status()
    print("백그라운드 관리자: " + ("실행 중" if running else "꺼짐"), flush=True)
    labels = {"starting":"서버 준비 중", "running":"서버 실행 중", "restarting":"예상치 못한 종료 후 재시작 중",
              "stopping":"정상 종료 중", "stopped":"정상 중지", "failed":"관리자 오류", "blocked_port":"다른 프로그램이 주소를 사용 중"}
    if state:
        print("마지막 상태: " + labels.get(state.get("state"), "확인 필요"), flush=True)
        print(f"자동 재시작: {state.get('restarts',0)}회 · 갱신: {state.get('updated_at','미확인')}", flush=True)
        if not running and state.get("state") not in {"stopped", "failed", "blocked_port"}:
            print("관리자가 예고 없이 종료됐습니다. 기록이 남지 않은 강제 종료·OS 종료 등은 구분할 수 없습니다.", flush=True)
    print(f"화면: {URL}", flush=True)
    print(f"종료·재시작 기록: {background.RUNTIME / 'lifecycle.jsonl'}", flush=True)
    print(f"서버 출력·오류: {background.RUNTIME / 'server.log'}", flush=True)
    if os.name == "nt":
        try:
            print("Windows 로그인 자동 시작: " + ("켜짐" if windows_startup.enabled() else "꺼짐"), flush=True)
            print("Windows 보관 키: " + ("등록됨" if credential_store.load_keys() else "없음"), flush=True)
        except (OSError, ValueError, RuntimeError):
            print("자동 시작/보관 키: 확인 실패. 자동시작 설정 파일을 확인해 주세요.", flush=True)
        print(f"로그인 자동 시작 기록: {background.RUNTIME / 'startup.log'}", flush=True)
    return 0


def stop_server():
    if not background.request_stop():
        print("실행 중인 백그라운드 관리자가 없습니다. 다른 프로그램은 종료하지 않았습니다.", flush=True)
        return 0
    print("정상 중지를 요청했습니다. 이 중지는 자동 재시작하지 않습니다.", flush=True)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and background.monitor_is_running():
        time.sleep(.25)
    if background.monitor_is_running():
        print("아직 종료 중입니다. ClaView_상태.cmd로 확인해 주세요.", flush=True)
        return 1
    print("서버와 백그라운드 관리자가 종료됐습니다.", flush=True)
    return 0


def main(argv=None):
    global BROWSER_ENABLED, UNATTENDED
    parser = argparse.ArgumentParser()
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--status", action="store_true")
    actions.add_argument("--stop", action="store_true")
    actions.add_argument("--setup-autostart", action="store_true", help="키 보안 보관 + 로그인 자동 시작")
    actions.add_argument("--disable-autostart", action="store_true", help="로그인 자동 시작만 해제")
    actions.add_argument("--forget-saved-keys", action="store_true", help="로그인 자동 시작 해제 + 보관 키 삭제")
    actions.add_argument("--foreground", action="store_true", help="개발용: 실행 창에 연결해 실행")
    parser.add_argument("--no-browser", action="store_true", help="브라우저를 새로 열지 않고 서버만 실행")
    parser.add_argument("--unattended", action="store_true", help="자동 시작: 키 입력 없이 보관 키 사용")
    args = parser.parse_args(argv)
    BROWSER_ENABLED = not (args.no_browser or args.unattended)
    UNATTENDED = args.unattended
    if args.unattended and args.foreground:
        parser.error("무인 실행과 콘솔 개발 모드는 함께 사용할 수 없습니다.")
    if args.status:
        return show_status()
    if args.stop:
        return stop_server()
    print("ClaView 서버 실행\n", flush=True)
    try:
        if args.setup_autostart:
            return setup_autostart()
        if args.disable_autostart or args.forget_saved_keys:
            windows_startup.disable()
            print("로그인 자동 시작을 해제했습니다. 현재 실행 중인 서버는 유지합니다.", flush=True)
            if args.forget_saved_keys:
                credential_store.delete_keys()
                print("이 프로젝트의 Windows 보관 키를 삭제했습니다.", flush=True)
                print("실행 중인 서버 메모리에서도 키를 지우려면 ClaView_중지.cmd를 실행하세요.", flush=True)
            return 0
        if background.monitor_is_running():
            print("관리자가 이미 켜져 있습니다. 새 관리자나 서버를 만들지 않습니다.", flush=True)
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                if server_is_running():
                    open_page()
                    return 0
                if not background.monitor_is_running():
                    break
                time.sleep(.5)
            raise RuntimeError("서버가 아직 준비되지 않았습니다. ClaView_상태.cmd를 확인해 주세요.")
        if server_is_running():
            print("서버가 이미 켜져 있습니다. 중복 실행하지 않고 화면만 엽니다. 이 서버는 새 관리자가 시작한 서버는 아닙니다.", flush=True)
            open_page()
            return 0
        with background.exclusive_lock(background.RUNTIME / "launch.lock"):
            print("화면을 준비합니다. 보관 키가 있으면 자동으로 읽고, 없으면 숨김 입력을 받습니다.", flush=True)
            print("키는 평문 파일에 저장하지 않습니다. 보안 보관 설정은 ClaView_자동시작_설정.cmd입니다.", flush=True)
            print("준비 완료 뒤 실행 창을 닫아도 서버가 유지됩니다.\n", flush=True)
            build_screen()
            return run_foreground() if args.foreground else run_server()
    except KeyboardInterrupt:
        print("\n실행을 취소했습니다.", flush=True)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"\n실행하지 못했습니다: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
