"""이 프로젝트의 키 두 개만 Windows 자격 증명 관리자에 저장한다.

평문 .env나 레지스트리에 키를 적지 않는다. 같은 Windows 사용자로 실행되는
다른 프로그램도 이 자격 증명에 접근할 수 있으므로 악성 프로그램 방어까지
보장하는 저장소는 아니다. 다른 계정/다른 PC로는 키가 따라가지 않는다.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path

import background

PROJECT_ID = hashlib.sha256(str(Path(__file__).resolve().parent).casefold().encode()).hexdigest()[:12]
TARGET = "ClaView/LocalServer/" + PROJECT_ID
MAX_BLOB = 2560
NOT_FOUND = 1168


class Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
    ]


def windows_api():
    if os.name != "nt":
        raise RuntimeError("키 보관은 Windows에서만 지원합니다.")
    dll = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    dll.CredWriteW.argtypes = [ctypes.POINTER(Credential), wintypes.DWORD]
    dll.CredWriteW.restype = wintypes.BOOL
    dll.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.POINTER(ctypes.POINTER(Credential))]
    dll.CredReadW.restype = wintypes.BOOL
    dll.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    dll.CredDeleteW.restype = wintypes.BOOL
    dll.CredFree.argtypes = [ctypes.c_void_p]
    dll.CredFree.restype = None
    return dll


def fail(action):
    # 원시 버퍼나 전달한 키가 오류/traceback에 섞이지 않게 숫자 오류만 설명한다.
    code = ctypes.get_last_error()
    raise RuntimeError(f"Windows 키 {action} 실패(오류 {code}). 같은 사용자로 다시 실행해 주세요.")


def save_keys(keys):
    background.checked_keys(keys)
    raw = json.dumps({"version": 1, "keys": keys}, ensure_ascii=False).encode("utf-8")
    if len(raw) > MAX_BLOB:
        raise ValueError("보관할 키의 길이가 Windows 저장 한도를 넘었습니다.")
    blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    cred = Credential(Type=1, TargetName=TARGET, Comment="ClaView local server API keys",
                      CredentialBlobSize=len(raw), CredentialBlob=blob,
                      Persist=2, UserName="ClaView")  # 같은 PC·같은 사용자, 다음 로그인에도 유지
    try:
        if not windows_api().CredWriteW(ctypes.byref(cred), 0):
            fail("저장")
    finally:
        ctypes.memset(blob, 0, len(blob))


def load_keys():
    api = windows_api()
    pointer = ctypes.POINTER(Credential)()
    if not api.CredReadW(TARGET, 1, 0, ctypes.byref(pointer)):
        if ctypes.get_last_error() == NOT_FOUND:
            return {}
        fail("읽기")
    try:
        size = pointer.contents.CredentialBlobSize
        if not 1 <= size <= MAX_BLOB:
            raise ValueError("저장한 키의 형식이 올바르지 않습니다. 키를 다시 설정해 주세요.")
        raw = ctypes.string_at(pointer.contents.CredentialBlob, size)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get("version") != 1:
                raise ValueError
            return dict(background.checked_keys(payload.get("keys")))
        except (ValueError, TypeError):
            raise ValueError("저장한 키의 형식이 올바르지 않습니다. 키를 다시 설정해 주세요.") from None
    finally:
        if pointer:
            if 0 < pointer.contents.CredentialBlobSize <= MAX_BLOB:
                ctypes.memset(pointer.contents.CredentialBlob, 0, pointer.contents.CredentialBlobSize)
            api.CredFree(pointer)


def delete_keys():
    api = windows_api()
    if not api.CredDeleteW(TARGET, 1, 0):
        if ctypes.get_last_error() == NOT_FOUND:
            return False
        fail("삭제")
    return True
