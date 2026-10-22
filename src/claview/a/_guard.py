"""Boundary guard: block imports of the old pipeline and block network transport.

Call ``install()`` before importing anything else from claview.a in a process that must stay inside the new
package (tests/a, child bootstrap). It installs

- an import hook at the front of ``sys.meta_path``. It refuses a module whose name is in the old package
  (every ``claview.*`` outside this package ``claview.a``: the B modules and the old A modules such as
  ``claview.aflow*`` and ``claview.ab_pipeline``) or an old evaluation module, and any module whose file is under
  the checkout's ``src/claview/`` (outside ``src/claview/a/``), ``eval/`` or ``scripts/``. The parent package
  ``claview`` itself (its folder entry and ``__init__.py``, a docstring only) is allowed, because importing
  ``claview.a`` imports it first; its other modules are not.
- an audit hook that refuses to open, compile or execute Python source files under those folders (direct
  ``exec`` of old source, or loading it by file location without the import system). Paths on both sides are
  resolved before the check: relative and bytes paths against the current working directory, then symbolic links,
  junctions and Win32 namespace prefixes (``realpath``); a path still on a UNC share is also compared by file
  identity. A hard link to an old file has its own name and is not recognised (not covered).
- network blocking: ``socket.socket.connect``/``connect_ex``/``sendto``, ``socket.getaddrinfo`` and
  ``urllib.request.urlopen`` are replaced, and the audit hook refuses the same socket and urllib events for
  sockets made any other way.

``install_network_block()`` alone is for tests_v3_compare, which imports the old modules on purpose.
Audit hooks cannot be removed, so the guard lives for the rest of the process.
Messages carry event and module names only, never addresses, URLs or headers.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import urllib.request

from .paths import ROOT

# Top-level packages refused with all their submodules. "claview" holds B and the old A modules in src/claview/.
# This package (OWN_PACKAGE, its modules) and its parent package "claview" itself are exempt.
FORBIDDEN_PACKAGES: tuple[str, ...] = ("claview",)
OWN_PACKAGE = "claview.a"
# Old evaluation modules (files directly under eval/), refused by name wherever they are found.
OLD_EVAL_MODULES: tuple[str, ...] = (
    "case_split", "judgment_runtime", "loss_causes", "q6g_candidates", "replay_client", "run_a",
    "run_aflow_stage0", "run_q6g_replay", "table_sets",
)
FORBIDDEN_DIRS: tuple = (ROOT / "src" / "claview", ROOT / "eval", ROOT / "scripts")
# Inside src/claview/ but not old code: this package's folder with everything in it, and the parent package's own
# folder entry, __init__.py and its cached bytecode (not the parent's other modules).
OWN_DIR = ROOT / "src" / "claview" / "a"
PARENT_DIR = ROOT / "src" / "claview"
SOURCE_SUFFIXES: tuple[str, ...] = (".py", ".pyc", ".pyw")

NETWORK_EVENTS = frozenset({
    "socket.connect", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo", "socket.gethostbyname",
    "socket.gethostbyname_ex", "socket.gethostbyaddr", "urllib.Request",
})

# Records of refused attempts (kept even if a caller swallows the exception).
VIOLATIONS: list[str] = []
NETWORK_ATTEMPTS: list[str] = []

_STATE = {"imports": False, "network": False, "hook": False}
_LOCAL = threading.local()


class ForbiddenImportError(ImportError):
    """An old-pipeline module or source file was requested inside the new-package boundary."""


class NetworkBlocked(RuntimeError):
    """A network transport was attempted while the guard is installed."""


# ---------------------------------------------------------------- predicates


# Win32 namespace prefixes, in normcase form (lower case, backslashes): \\?\UNC\, \\.\UNC\, \??\UNC\ become the
# plain UNC start \\, and \\?\, \\.\, \??\ are dropped.
_UNC = "\\\\"
_NAMESPACE_PREFIXES: tuple[tuple[str, str], ...] = (
    (r"\\?\unc" + "\\", _UNC), (r"\\.\unc" + "\\", _UNC), (r"\??\unc" + "\\", _UNC),
    (r"\\?" + "\\", ""), (r"\\." + "\\", ""), (r"\??" + "\\", ""),
)


def _strip_namespace(path: str) -> str:
    if os.name != "nt":
        return path
    for prefix, replacement in _NAMESPACE_PREFIXES:
        if path.startswith(prefix):
            return replacement + path[len(prefix):]
    return path


def _norm(path) -> str | None:
    """Resolved, case-normalised form of a str / bytes / path-like path; None for anything else.

    The path is resolved with ``realpath`` (symbolic links, junctions, Win32 namespace prefixes, trailing dots
    and spaces, ``::$DATA`` stream names), case-normalised, and any namespace prefix left by
    the resolution is stripped. A relative path is resolved against the current working directory at the time of
    the check, which is the directory the open/compile/exec call resolves it against. realpath queries the file
    system but opens no Python-level file, so it raises no audit event the hook would act on. A file descriptor
    (int) carries no path: the call that produced it (``os.open``, ``open``) was already checked with its path.
    Pseudo file names such as ``<string>`` pass.
    """
    if isinstance(path, bool) or isinstance(path, int):
        return None
    try:
        text = os.fsdecode(os.fspath(path))
    except (TypeError, ValueError):
        return None
    if not text or "\x00" in text or (text.startswith("<") and text.endswith(">")):
        return None
    try:
        resolved = os.path.realpath(text)
    except (OSError, ValueError):
        try:
            resolved = os.path.abspath(text)
        except (OSError, ValueError):
            return None
    return _strip_namespace(os.path.normcase(resolved))


def _identity(path: str):
    """(device, file id) of an existing path, or None."""
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return (st.st_dev, st.st_ino) if st.st_ino else None


def _under_by_identity(p: str, bases: list[str]) -> bool:
    """A UNC path (for example a loopback administrative share) can name a local folder: compare file identities
    of the path and each of its parents with the forbidden folders."""
    wanted = {i for i in (_identity(b) for b in bases) if i is not None}
    if not wanted:
        return False
    current = p
    while True:
        if _identity(current) in wanted:
            return True
        parent = os.path.dirname(current)
        if parent == current or not parent:
            return False
        current = parent


_FORBIDDEN_BASES: list[str] = []


def _bases(dirs) -> list[str]:
    """Resolved folders; the module's own forbidden folders are resolved once."""
    if dirs is FORBIDDEN_DIRS:
        if not _FORBIDDEN_BASES:
            _FORBIDDEN_BASES.extend(b for b in (_norm(d) for d in dirs) if b is not None)
        return _FORBIDDEN_BASES
    return [b for b in (_norm(d) for d in dirs) if b is not None]


_EXEMPT_BASES: list[str] = []


def _exempt(p: str) -> bool:
    """A resolved path that is this package (OWN_DIR or inside it) or the parent package's own entry (PARENT_DIR,
    its __init__.py, its cached __init__ bytecode). A UNC path is compared with OWN_DIR by file identity."""
    if not _EXEMPT_BASES:
        _EXEMPT_BASES.extend(b for b in (_norm(OWN_DIR), _norm(PARENT_DIR)) if b is not None)
    if len(_EXEMPT_BASES) != 2:
        return False
    own, parent = _EXEMPT_BASES
    if p == own or p.startswith(own.rstrip(os.sep) + os.sep):
        return True
    if p in (parent, os.path.join(parent, "__init__.py")):
        return True
    if os.path.dirname(p) == os.path.join(parent, "__pycache__") and os.path.basename(p).startswith("__init__."):
        return True
    return p.startswith(_UNC) and _under_by_identity(p, [own])


def is_under(path, dirs=FORBIDDEN_DIRS) -> bool:
    """True if path is one of dirs or inside one (component-wise, so src/claview_flow is not src/claview), and is
    not exempt (``_exempt``: this package and the parent package's own entry).

    Both sides are resolved the same way (``_norm``). A path that is still a UNC path after resolution is also
    compared by file identity.
    """
    p = _norm(path)
    if p is None:
        return False
    bases = _bases(dirs)
    inside = any(p == base or p.startswith(base.rstrip(os.sep) + os.sep) for base in bases)
    if not inside:
        inside = p.startswith(_UNC) and _under_by_identity(p, bases)
    return inside and not _exempt(p)


def _within(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")


def is_forbidden_name(fullname: str, packages=FORBIDDEN_PACKAGES, modules=OLD_EVAL_MODULES) -> bool:
    """This package and its modules, and its parent packages (``claview``), are allowed; every other module of the
    forbidden packages and the old evaluation modules are refused."""
    if _within(fullname, OWN_PACKAGE) or OWN_PACKAGE.startswith(fullname + "."):
        return False
    if any(_within(fullname, p) for p in packages):
        return True
    return fullname in modules


def is_forbidden_source(path, dirs=FORBIDDEN_DIRS) -> bool:
    p = _norm(path)
    return p is not None and p.endswith(SOURCE_SUFFIXES) and is_under(p, dirs)


def module_is_forbidden(name: str, module, dirs=FORBIDDEN_DIRS) -> bool:
    if is_forbidden_name(name):
        return True
    locations = [getattr(module, "__file__", None)]
    locations += list(getattr(module, "__path__", None) or [])
    return any(isinstance(loc, str) and is_under(loc, dirs) for loc in locations)


def loaded_forbidden() -> list[str]:
    """Names of forbidden modules already in sys.modules."""
    return sorted(name for name, module in list(sys.modules.items()) if module_is_forbidden(name, module))


def problems() -> list[str]:
    """Everything the guard saw: forbidden modules now loaded, refused imports/sources, network attempts."""
    return ([f"loaded: {n}" for n in loaded_forbidden()] + [f"refused: {v}" for v in VIOLATIONS]
            + [f"network: {n}" for n in NETWORK_ATTEMPTS])


# ---------------------------------------------------------------- import hook


class ImportGuard:
    """Meta-path finder that refuses forbidden module names and modules located under forbidden folders.

    It never loads anything: for an allowed module it returns None and lets the normal finders run.
    """

    def __init__(self, dirs=FORBIDDEN_DIRS, packages=FORBIDDEN_PACKAGES, modules=OLD_EVAL_MODULES, sink=None):
        self.dirs, self.packages, self.modules = tuple(dirs), tuple(packages), tuple(modules)
        self.sink = VIOLATIONS if sink is None else sink

    def _deny(self, fullname: str, why: str):
        self.sink.append(f"import {fullname} ({why})")
        raise ForbiddenImportError(f"claview.a boundary: import of {fullname!r} refused ({why})", name=fullname)

    def find_spec(self, fullname, path=None, target=None):
        if is_forbidden_name(fullname, self.packages, self.modules):
            self._deny(fullname, "forbidden module name")
        if getattr(_LOCAL, "busy", False):
            return None
        _LOCAL.busy = True
        try:
            spec = None
            for finder in list(sys.meta_path):
                if isinstance(finder, ImportGuard):
                    continue
                find = getattr(finder, "find_spec", None)
                if find is None:
                    continue
                spec = find(fullname, path, target)
                if spec is not None:
                    break
        finally:
            _LOCAL.busy = False
        if spec is None:
            return None
        # origin is a file only when has_location ("built-in" / "frozen" are markers, not relative paths)
        locations = ([spec.origin] if spec.has_location else []) + list(spec.submodule_search_locations or [])
        if any(isinstance(loc, str) and is_under(loc, self.dirs) for loc in locations):
            self._deny(fullname, "module file under a forbidden folder")
        return None


# ---------------------------------------------------------------- audit hook and network blocking


def _refuse_network(event: str):
    NETWORK_ATTEMPTS.append(event)
    raise NetworkBlocked(f"claview.a boundary: network transport refused ({event})")


def _audit(event: str, args) -> None:
    if _STATE["network"] and event in NETWORK_EVENTS:
        _refuse_network(event)
    if not _STATE["imports"]:
        return
    if event == "open" and args and is_forbidden_source(args[0]):
        VIOLATIONS.append("open old source")
        raise ForbiddenImportError("claview.a boundary: reading old Python source refused")
    if event == "compile" and len(args) > 1 and is_forbidden_source(args[1]):
        VIOLATIONS.append("compile old source")
        raise ForbiddenImportError("claview.a boundary: compiling old Python source refused")
    if event == "exec" and args and is_forbidden_source(getattr(args[0], "co_filename", None)):
        VIOLATIONS.append("exec old source")
        raise ForbiddenImportError("claview.a boundary: executing old Python source refused")


def _install_hook() -> None:
    if not _STATE["hook"]:
        sys.addaudithook(_audit)
        _STATE["hook"] = True


def _blocked(event: str):
    def blocked(*args, **kwargs):
        _refuse_network(event)

    blocked.__name__ = f"blocked_{event.replace('.', '_')}"
    return blocked


def install_network_block() -> None:
    """Replace socket/urllib transport entry points and refuse the matching audit events."""
    if _STATE["network"]:
        return
    socket.socket.connect = _blocked("socket.connect")
    socket.socket.connect_ex = _blocked("socket.connect_ex")
    socket.socket.sendto = _blocked("socket.sendto")
    socket.getaddrinfo = _blocked("socket.getaddrinfo")
    urllib.request.urlopen = _blocked("urllib.request.urlopen")
    _STATE["network"] = True
    _install_hook()


def install_import_guard() -> None:
    """Fail if a forbidden module is already loaded, then put the import hook first and refuse old sources."""
    found = loaded_forbidden()
    if found:
        raise ForbiddenImportError(f"claview.a boundary: forbidden modules already loaded: {found}")
    if not any(isinstance(f, ImportGuard) for f in sys.meta_path):
        sys.meta_path.insert(0, ImportGuard())
    _STATE["imports"] = True
    _install_hook()


def install() -> None:
    """Full guard: import boundary and network blocking."""
    install_import_guard()
    install_network_block()


def installed() -> dict:
    return dict(_STATE)
