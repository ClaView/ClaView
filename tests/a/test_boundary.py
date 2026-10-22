"""Boundary of the new package (plan 02, 3-3): static scan, child-process run, and counterexamples.

Counterexamples must be caught: collection-time import, already-loaded module, direct execution of old source,
transport attempt in a child process, dynamic import, and an import at package-initialisation time.
No old module is executed by these tests; probes either stop at the guard or use empty stand-in packages.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
import runpy
import shutil
import socket
import subprocess
import sys
import types
import urllib.request
from pathlib import Path

import pytest

from claview.a import _guard
from claview.a._guard import ForbiddenImportError, ImportGuard, NetworkBlocked

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
PKG = SRC / "claview" / "a"
TESTS_A = Path(__file__).resolve().parent
OLD_SOURCE = SRC / "claview" / "aflow" / "rules.py"

# ---------------------------------------------------------------- static scan

# Approved plan 17 shared package only; old claview/B imports remain forbidden.
ALLOWED_THIRD_PARTY = {"langgraph", "langchain_core", "claview_period"}
BANNED_MODULES = {
    "importlib", "runpy", "imp", "pkgutil", "zipimport", "inspect", "linecache", "code", "codeop",
    "py_compile", "compileall", "builtins", "marshal",
}
BANNED_NAMES = {"exec", "eval", "compile", "__import__", "__builtins__"}
BANNED_ATTRS = {"__import__", "spec_from_file_location", "exec_module", "load_module", "run_path", "run_module",
                "import_module"}
READ_CALLS = {"open", "read_text", "read_bytes", "glob", "rglob", "iterdir", "listdir", "scandir", "walk",
              "open_code"}


def _has_py_constant(node) -> bool:
    return any(isinstance(n, ast.Constant) and isinstance(n.value, str) and ".py" in n.value for n in ast.walk(node))


def scan(text: str, depth: int = 1) -> list[str]:
    """Problems in one module of the new package. depth = package depth (claview.a = 1, claview.a.reused = 2)."""
    out = []
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out += _check_absolute(alias.name, node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level > depth:
                    out.append(f"{node.lineno}: relative import leaves claview.a")
            else:
                out += _check_absolute(node.module or "", node.lineno)
                if node.module == "sys" and any(a.name == "path" for a in node.names):
                    out.append(f"{node.lineno}: sys.path import")
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            out.append(f"{node.lineno}: name {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr in BANNED_ATTRS:
                out.append(f"{node.lineno}: attribute {node.attr}")
            if node.attr == "path" and isinstance(node.value, ast.Name) and node.value.id == "sys":
                out.append(f"{node.lineno}: sys.path use")
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name in READ_CALLS and _has_py_constant(node):
                out.append(f"{node.lineno}: reads a .py file")
    return out


def _check_absolute(module: str, lineno: int) -> list[str]:
    top = module.split(".")[0]
    if module == "claview.a" or module.startswith("claview.a."):
        return [f"{lineno}: absolute import of claview.a (use relative)"]
    if top in BANNED_MODULES:
        return [f"{lineno}: banned module {top}"]
    if top in ALLOWED_THIRD_PARTY or top in sys.stdlib_module_names:
        return []
    return [f"{lineno}: import outside the allowed set: {module}"]


def _package_files():
    files = sorted(PKG.rglob("*.py"))
    assert files, "no package files found"
    return files


def test_package_sources_pass_static_scan():
    problems = {}
    for path in _package_files():
        depth = len(path.relative_to(PKG).parts)
        found = scan(path.read_text(encoding="utf-8"), depth)
        if found:
            problems[str(path.relative_to(ROOT))] = found
    assert problems == {}


def test_package_init_files_are_empty():
    inits = [p for p in _package_files() if p.name == "__init__.py"]
    assert PKG / "__init__.py" in inits
    assert {str(p.relative_to(ROOT)): p.read_text(encoding="utf-8").strip() for p in inits} == {
        str(p.relative_to(ROOT)): "" for p in inits
    }
    # The parent package claview is imported before this package and is exempt from the guard: it may hold only a
    # docstring (no import, no code).
    parent = ast.parse((PKG.parent / "__init__.py").read_text(encoding="utf-8"))
    assert all(isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) for n in parent.body)


@pytest.mark.parametrize("snippet", [
    "import claview.aflow.rules",
    "from claview.fetch import x",
    "import table_sets",
    "import numpy",
    "import claview.a.paths",
    "import importlib",
    "from importlib import util",
    "import runpy",
    "x = __import__('claview')",
    "spec = util.spec_from_file_location('m', 'p')",
    "import sys\nsys.path.insert(0, 'src')",
    "from sys import path",
    "exec('x = 1')",
    "eval('1')",
    "c = compile('1', 'f', 'eval')",
    "open('src/claview/aflow/rules.py').read()",
    "from pathlib import Path\nPath('old.py').read_text()",
    "from pathlib import Path\nlist(Path('.').glob('*.py'))",
    "from . import x\nfrom ... import y",
])
def test_static_scan_catches_counterexamples(snippet):
    assert scan(snippet, depth=2) != []


def test_static_scan_allows_plain_code():
    ok = "from __future__ import annotations\nimport re\nfrom .paths import ROOT\nfrom langgraph.graph import END\n" \
         "P = re.compile('x')\nSUFFIX = ('.py',)\n"
    assert scan(ok, depth=1) == []


# ---------------------------------------------------------------- in-process guard state


def test_guard_installed_before_collection():
    state = _guard.installed()
    assert state == {"imports": True, "network": True, "hook": True}
    assert isinstance(sys.meta_path[0], ImportGuard)
    assert _guard.loaded_forbidden() == []


def test_forbidden_dirs_are_component_wise():
    assert _guard.is_under(SRC / "claview" / "aflow" / "x.py")
    assert _guard.is_under(ROOT / "scripts" / "x.py") and _guard.is_under(ROOT / "eval" / "x.py")
    assert not _guard.is_under(PKG / "paths.py")
    assert not _guard.is_forbidden_source(ROOT / "eval" / "cases" / "mini_sets_v1.json")
    # src/claview/ outside this package stays forbidden (B and old A); the parent's own entry and __init__ do not.
    assert _guard.is_under(SRC / "claview" / "fetch.py") and _guard.is_under(SRC / "claview" / "b" / "x.py")
    assert _guard.is_under(SRC / "claview" / "aflow") and _guard.is_under(SRC / "claview" / "ab_pipeline.py")
    assert not _guard.is_under(SRC / "claview") and not _guard.is_under(SRC / "claview" / "__init__.py")
    cache = SRC / "claview" / "__pycache__"  # only the parent __init__'s own bytecode names are exempt
    assert not _guard.is_under(cache / f"__init__.{sys.implementation.cache_tag}.pyc")
    assert _guard.is_forbidden_source(cache / "__init__.probe.py") and _guard.is_forbidden_source(cache / "__init__.x.pyw")
    assert _guard.is_forbidden_source(cache / f"fetch.{sys.implementation.cache_tag}.pyc")
    assert not _guard.is_under(PKG) and not _guard.is_under(PKG / "reused" / "x.py")
    assert not any(_guard.is_forbidden_name(n) for n in ("claview", "claview.a", "claview.a.x", "claview.a.reused.x"))
    assert all(_guard.is_forbidden_name(n) for n in ("claview.fetch", "claview.b", "claview.aflow", "claview.ab_pipeline",
                                                     "claview.a1_graph", "claview.aflow.rules"))


# ---------------------------------------------------------------- counterexample: dynamic import


@pytest.mark.parametrize("name", ["claview.fetch", "claview.aflow", "claview.aflow.rules", "claview.ab_pipeline",
                                  "claview.b.contract", "table_sets", "judgment_runtime", "replay_client",
                                  "q6g_candidates"])
def test_dynamic_import_by_name_is_refused(refusals, name):
    with pytest.raises(ForbiddenImportError):
        importlib.import_module(name)
    with pytest.raises(ForbiddenImportError):
        __import__(name)
    assert name not in sys.modules
    assert refusals.violations()


def test_import_by_folder_is_refused(tmp_path, refusals):
    """Path rule: a module found under a forbidden folder is refused whatever its name."""
    old = tmp_path / "old_folder"
    old.mkdir()
    (old / "zz_v3_probe_mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    sink: list = []
    guard = ImportGuard(dirs=[old], sink=sink)
    sys.meta_path.insert(0, guard)
    sys.path.insert(0, str(old))
    try:
        with pytest.raises(ForbiddenImportError):
            importlib.import_module("zz_v3_probe_mod")
    finally:
        sys.meta_path.remove(guard)
        sys.path.remove(str(old))
        sys.modules.pop("zz_v3_probe_mod", None)
    assert sink and "zz_v3_probe_mod" not in sys.modules


def test_load_by_file_location_is_refused(refusals):
    spec = importlib.util.spec_from_file_location("zz_v3_by_location", OLD_SOURCE)
    module = importlib.util.module_from_spec(spec)
    with pytest.raises(ForbiddenImportError):
        spec.loader.exec_module(module)
    assert "open old source" in refusals.violations()


# ---------------------------------------------------------------- counterexample: direct execution of old source


def test_reading_old_source_is_refused(refusals):
    with pytest.raises(ForbiddenImportError):
        OLD_SOURCE.read_text(encoding="utf-8")
    with pytest.raises(ForbiddenImportError):
        runpy.run_path(str(OLD_SOURCE))


def test_compiling_or_executing_old_source_is_refused(refusals):
    with pytest.raises(ForbiddenImportError):
        compile("x = 1", str(OLD_SOURCE), "exec")
    code = compile("x = 1", "<v3-probe>", "exec").replace(co_filename=str(OLD_SOURCE))
    with pytest.raises(ForbiddenImportError):
        exec(code, {})
    assert {"compile old source", "exec old source"} <= set(refusals.violations())


OLD_EVAL_SOURCE = ROOT / "eval" / "table_sets.py"


def test_relative_paths_to_old_source_are_refused(refusals, monkeypatch):
    """Relative names (str, bytes, path-like, os.open) resolve against the working directory before the check."""
    if not OLD_EVAL_SOURCE.exists():
        pytest.skip("eval/table_sets.py absent")
    monkeypatch.chdir(ROOT)
    rel = "eval/table_sets.py"
    for opener in (lambda: open(rel, encoding="utf-8"), lambda: open(rel.encode(), "rb"),
                   lambda: open(Path(rel), "rb"), lambda: os.open(rel, os.O_RDONLY),
                   lambda: Path(rel).read_text(encoding="utf-8"), lambda: runpy.run_path(rel)):
        with pytest.raises(ForbiddenImportError):
            opener()
    with pytest.raises(ForbiddenImportError):
        compile("x = 1", rel, "exec")  # the same relative name as the open above
    with pytest.raises(ForbiddenImportError):
        compile("x = 1", rel.encode(), "exec")
    code = compile("x = 1", "<v3-probe>", "exec").replace(co_filename=rel)
    with pytest.raises(ForbiddenImportError):
        exec(code, {})
    assert {"open old source", "compile old source", "exec old source"} <= set(refusals.violations())


def test_relative_paths_after_chdir_are_refused(refusals, monkeypatch):
    if not OLD_EVAL_SOURCE.exists():
        pytest.skip("eval/table_sets.py absent")
    monkeypatch.chdir(ROOT / "eval")
    for name in ("table_sets.py", "./table_sets.py", os.path.join("..", "eval", "table_sets.py")):
        with pytest.raises(ForbiddenImportError):
            open(name, "rb")
        with pytest.raises(ForbiddenImportError):
            compile("x = 1", name, "exec")
        code = compile("x = 1", "<v3-probe>", "exec").replace(co_filename=name)
        with pytest.raises(ForbiddenImportError):
            exec(code, {})
    monkeypatch.chdir(ROOT / "src")
    with pytest.raises(ForbiddenImportError):
        open(os.path.join("claview", "aflow", "rules.py"), "rb")
    assert {"open old source", "compile old source", "exec old source"} <= set(refusals.violations())


def _readers(path):
    """Every way the hook must stop: open, os.open, Path.open, read_text, compile and exec by that name."""
    code = compile("x = 1", "<v3-probe>", "exec").replace(co_filename=str(path))
    return {
        "open": lambda: open(path, "rb"),
        "os.open": lambda: os.open(path, os.O_RDONLY),
        "Path.open": lambda: Path(path).open("rb"),
        "read_text": lambda: Path(path).read_text(encoding="utf-8"),
        "compile": lambda: compile("x = 1", str(path), "exec"),
        "exec": lambda: exec(code, {}),
    }


def _assert_all_refused(path, refusals):
    for how, reader in _readers(path).items():
        with pytest.raises(ForbiddenImportError):
            result = reader()
            if isinstance(result, int):
                os.close(result)
            elif hasattr(result, "close"):
                result.close()
            pytest.fail(f"{how} was not refused")
    assert {"open old source", "compile old source", "exec old source"} <= set(refusals.violations())


WIN_ONLY = pytest.mark.skipif(os.name != "nt", reason="Win32 namespace and junction forms exist only on Windows")


@WIN_ONLY
@pytest.mark.parametrize("prefix", ["\\\\?\\", "\\\\.\\", "//?/", "\\??\\"])
def test_namespace_prefixed_old_source_is_refused(refusals, prefix):
    """\\\\?\\D:\\..., \\\\.\\D:\\... and their variants name the same file as the plain path."""
    _assert_all_refused(prefix + str(OLD_SOURCE), refusals)


@WIN_ONLY
def test_stream_and_trailing_dot_names_of_old_source_are_refused(refusals):
    for alias in (str(OLD_SOURCE) + "::$DATA", str(OLD_SOURCE) + ".", str(OLD_SOURCE) + " "):
        with pytest.raises(ForbiddenImportError):
            open(alias, "rb")
    assert "open old source" in refusals.violations()


@WIN_ONLY
def test_unc_loopback_share_to_old_source_is_refused(refusals):
    """\\\\?\\UNC\\localhost\\D$\\... (administrative share) is compared by file identity."""
    drive, rest = os.path.splitdrive(str(OLD_SOURCE))
    if len(drive) != 2:
        pytest.skip("checkout is not on a drive letter")
    unc = "\\\\?\\UNC\\localhost\\" + drive[0] + "$" + rest
    try:
        reachable = os.path.exists(unc)
    except OSError:
        reachable = False
    if not reachable:
        pytest.skip("loopback administrative share not reachable here")
    _assert_all_refused(unc, refusals)
    # The same share keeps the parent package's own entry and this package allowed, B refused (file identity).
    parent_unc = "\\\\?\\UNC\\localhost\\" + drive[0] + "$" + os.path.splitdrive(str(SRC / "claview"))[1]
    assert not _guard.is_under(parent_unc) and not _guard.is_under(parent_unc + "\\__init__.py")
    assert not _guard.is_under(parent_unc + "\\a\\paths.py") and _guard.is_under(parent_unc + "\\fetch.py")


def _make_dir_link(link: Path, target: Path) -> str | None:
    """Junction (no admin right needed) or directory symlink to target; returns the kind made or None."""
    if os.name == "nt":
        try:
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
            return "junction"
        except (ImportError, AttributeError, OSError):
            pass
    try:
        os.symlink(target, link, target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError):
        return None


def test_link_to_old_folder_is_refused(tmp_path, refusals):
    """A junction or symlink in a temporary folder that points at src/claview is resolved to its target."""
    link = tmp_path / "old_link"
    kind = _make_dir_link(link, SRC / "claview")
    if kind is None:
        pytest.skip("neither a junction nor a directory symlink can be made here")
    try:
        _assert_all_refused(link / OLD_SOURCE.relative_to(SRC / "claview"), refusals)
    finally:
        os.rmdir(link)  # removes the link only, never the target
    assert (SRC / "claview").is_dir()


def test_file_symlink_with_other_suffix_to_old_source_is_refused(tmp_path, refusals):
    alias = tmp_path / "rules.txt"
    try:
        os.symlink(OLD_SOURCE, alias)
    except (OSError, NotImplementedError):
        pytest.skip("file symlinks need developer mode or admin right here")
    try:
        with pytest.raises(ForbiddenImportError):
            open(alias, "rb")
    finally:
        os.unlink(alias)


def test_link_to_allowed_folder_stays_readable(tmp_path):
    """Resolution does not over-refuse: a link to a temporary folder with a .py file reads normally."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "ok_probe.py").write_text("X = 1\n", encoding="utf-8")
    link = tmp_path / "ok_link"
    if _make_dir_link(link, real) is None:
        pytest.skip("neither a junction nor a directory symlink can be made here")
    try:
        assert (link / "ok_probe.py").read_text(encoding="utf-8") == "X = 1\n"
        exec(compile("x = 1", str(link / "ok_probe.py"), "exec"), {})
        if os.name == "nt":
            with open("\\\\?\\" + str(real / "ok_probe.py"), "rb") as stream:
                assert stream.read(1) == b"X"
    finally:
        os.rmdir(link)


def test_relative_paths_outside_old_folders_stay_allowed(monkeypatch, tmp_path):
    """A relative source path outside the forbidden folders, pseudo file names and fd numbers are not refused."""
    monkeypatch.chdir(tmp_path)
    Path("ok_probe.py").write_text("X = 1\n", encoding="utf-8")
    with open("ok_probe.py", encoding="utf-8") as stream:
        assert stream.read() == "X = 1\n"
    exec(compile("x = 1", "ok_probe.py", "exec"), {})
    exec(compile("x = 1", "<string>", "exec"), {})
    fd = os.open("ok_probe.py", os.O_RDONLY)
    with open(fd, "rb", closefd=True) as stream:
        assert stream.read(1) == b"X"
    assert not _guard.is_forbidden_source("built-in") and not _guard.is_under("frozen")


def test_data_files_under_eval_stay_readable():
    roles = ROOT / "eval" / "cases" / "case_roles_v2.json"
    if not roles.exists():
        pytest.skip("case roles file absent")
    with roles.open("rb") as stream:
        assert stream.read(1)


# ---------------------------------------------------------------- counterexample: already-loaded module


def test_already_loaded_module_is_detected_in_process():
    for name, module in [("claview.aflow.zz_fake", types.ModuleType("claview.aflow.zz_fake")),
                         ("zz_fake_old_file", types.ModuleType("zz_fake_old_file"))]:
        if name == "zz_fake_old_file":
            module.__file__ = str(ROOT / "scripts" / "zz_fake_old_file.py")
        sys.modules[name] = module
        try:
            assert name in _guard.loaded_forbidden()
            with pytest.raises(ForbiddenImportError):
                _guard.install_import_guard()
        finally:
            del sys.modules[name]
    assert _guard.loaded_forbidden() == []


# ---------------------------------------------------------------- network


def test_network_is_blocked_in_process(refusals):
    with pytest.raises(NetworkBlocked):
        socket.create_connection(("127.0.0.1", 9), timeout=0.1)
    with pytest.raises(NetworkBlocked):
        urllib.request.urlopen("http://127.0.0.1:9/")
    assert refusals.network()


# ---------------------------------------------------------------- child processes


def _child_env(pythonpath: list) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k != "PYTHONPATH" and not any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET"))}
    env["PYTHONPATH"] = os.pathsep.join(str(p) for p in pythonpath)
    env["PYTHONHASHSEED"] = "0"
    return env


def _run_child(code: str, pythonpath: list, cwd, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", code, *map(str, args)], cwd=cwd,
                          env=_child_env(pythonpath), capture_output=True, text=True, encoding="utf-8",
                          timeout=180)


CHILD_ENTRY = r'''
import claview.a._guard as g
g.install()
import json, os, sys, sysconfig
from claview.a import paths
from claview.a.reused import judgment_runtime, model, node_contract, q_nodes, q_rules, table_sets

q = "2024년 1분기부터 매달 수출액을  보여줘"
n = q_nodes.normalize(q)
rows = q_nodes.frequencies(q, n["query"], n["offsets"])
assert [r["value"] for r in rows] == ["M"], rows
assert q_rules.find_periods(n["query"], None)

class Rt:
    def __init__(self, context): self.context = context
    def override(self, **kw): return Rt(kw["context"])

node = node_contract.wrap("Q1", lambda s, r: q_nodes.q1(s, r), None, node_writes={"Q1": {"query", "query_nospace", "offsets"}})
assert node({"question": q}, Rt(None))["query"] == n["query"]
assert table_sets.score_sets([["A"]], ["A"], ["A"], ["A"], 5)["set_exact"]
assert paths.ROOT.name

from claview.a.contract import check, shapes
from claview.a.interpret import conditions, nodes, pipeline
from claview.a.interpret.text import normalize_text
from claview.a.select import evidence
text = normalize_text(q)
found = conditions.find_frequency_conditions(text)
assert [c["value"] for c in found] == ["M"] and check.check_conditions(found, q) == [], found
analysis = pipeline.interpret_question(q)  # runs inside the boundary and returns the whole Analysis (plan 04, 2.1-2.7)
assert analysis["interpretation"] is not None and check.check_analysis(analysis, q) == [], analysis

pkg = os.path.normcase(os.path.abspath(sys.argv[1]))
roots = [pkg]
sp = sysconfig.get_paths()
for key in ("stdlib", "platstdlib", "purelib", "platlib"):
    roots.append(os.path.normcase(os.path.abspath(sp[key])))
roots.append(os.path.normcase(os.path.abspath(os.path.join(sys.base_prefix, "DLLs"))))
parent = os.path.dirname(pkg)  # the parent package claview itself (folder entry and docstring-only __init__)
parent_entry = {parent, os.path.join(parent, "__init__.py")}

def inside(f):
    f = os.path.normcase(os.path.abspath(f))
    return f in parent_entry or any(f == r or f.startswith(r.rstrip(os.sep) + os.sep) for r in roots)

bad = []
for name, mod in list(sys.modules.items()):
    files = [getattr(mod, "__file__", None)] + list(getattr(mod, "__path__", None) or [])
    if any(isinstance(f, str) and not inside(f) for f in files):
        bad.append(name)
outside = sorted(m for m in sys.modules if m.startswith("claview.") and not (m == "claview.a" or m.startswith("claview.a.")))
print(json.dumps({"bad": sorted(bad), "problems": g.problems(), "claview": outside}))
'''


def test_child_runs_entry_points_inside_boundary(tmp_path):
    result = _run_child(CHILD_ENTRY, [SRC], tmp_path, PKG)
    assert result.returncode == 0, result.stderr[-2000:]
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report == {"bad": [], "problems": [], "claview": []}


CHILD_SEND = r'''
import claview.a._guard as g
g.install()
import _socket, json, socket, urllib.request
from claview.a.reused.model import HcxClient

def attempt(fn):
    try:
        fn()
    except BaseException as error:
        return type(error).__name__
    return "SENT"

def raw_connect():
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    try:
        s.connect(("127.0.0.1", 9))
    finally:
        s.close()

def tcp():
    s = socket.socket()
    try:
        s.connect(("127.0.0.1", 9))
    finally:
        s.close()

def tcp_ex():
    s = socket.socket()
    try:
        s.connect_ex(("127.0.0.1", 9))
    finally:
        s.close()

def udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.sendto(b"x", ("127.0.0.1", 9))
    finally:
        s.close()

out = {
    "create_connection": attempt(lambda: socket.create_connection(("127.0.0.1", 9), timeout=0.1)),
    "connect": attempt(tcp),
    "connect_ex": attempt(tcp_ex),
    "sendto": attempt(udp),
    "raw_socket_connect": attempt(raw_connect),
    "getaddrinfo": attempt(lambda: socket.getaddrinfo("localhost", 80)),
    "urlopen": attempt(lambda: urllib.request.urlopen("http://127.0.0.1:9/")),
    "hcx_default_opener": attempt(lambda: HcxClient("fake-key", min_interval=0).complete("T2", "s", {}, {})),
}
print(json.dumps({"out": out, "attempts": len(g.NETWORK_ATTEMPTS)}))
'''


def test_child_transport_attempts_are_blocked(tmp_path):
    result = _run_child(CHILD_SEND, [SRC], tmp_path)
    assert result.returncode == 0, result.stderr[-2000:]
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert set(report["out"].values()) == {"NetworkBlocked"}, report["out"]
    assert report["attempts"] >= len(report["out"])


def _write_probe(tmp_path: Path, body: str) -> Path:
    probe = tmp_path / "test_zz_v3_probe.py"
    probe.write_text(body + "\n\ndef test_probe():\n    assert True\n", encoding="utf-8")
    return probe


def _run_pytest_with_conftest(tmp_path: Path, probe: Path, prelude: str = "") -> subprocess.CompletedProcess:
    """Child pytest that loads the real tests/a/conftest.py as a plugin, then collects only the probe."""
    code = prelude + "import sys, pytest\nsys.exit(pytest.main(sys.argv[1:]))\n"
    return _run_child(code, [TESTS_A], tmp_path, "-q", "-p", "no:cacheprovider", "-p", "conftest",
                      "--rootdir", tmp_path, str(probe))


def test_probe_harness_runs_an_allowed_probe(tmp_path):
    probe = _write_probe(tmp_path, "from claview.a.reused import q_rules  # allowed")
    result = _run_pytest_with_conftest(tmp_path, probe)
    assert result.returncode == 0, (result.stdout + result.stderr)[-2000:]


@pytest.mark.parametrize("body", [
    "from claview.aflow import rules",
    "import claview.fetch",
    "import claview.ab_pipeline",
    "import table_sets",
])
def test_collection_time_import_fails(tmp_path, body):
    probe = _write_probe(tmp_path, body)
    result = _run_pytest_with_conftest(tmp_path, probe)
    assert result.returncode != 0
    assert "ForbiddenImportError" in result.stdout + result.stderr


@pytest.mark.parametrize("by", ["name", "file"])
def test_already_loaded_module_fails_before_collection(tmp_path, by):
    """A module loaded before conftest (stand-in module objects, nothing old runs) stops the session."""
    probe = _write_probe(tmp_path, "X = 1")
    if by == "name":
        prelude = "import sys, types\nsys.modules['claview.aflow.zz_fake'] = types.ModuleType('claview.aflow.zz_fake')\n"
    else:
        prelude = ("import sys, types\nm = types.ModuleType('zz_fake_old')\n"
                   f"m.__file__ = {str(ROOT / 'eval' / 'zz_fake_old.py')!r}\nsys.modules['zz_fake_old'] = m\n")
    result = _run_pytest_with_conftest(tmp_path, probe, prelude)
    assert result.returncode != 0
    assert "forbidden modules loaded before collection" in result.stdout + result.stderr


def test_package_init_time_import_is_caught(tmp_path):
    """If the package __init__ imported an old module, the bootstrap's install() must fail."""
    copy_src = tmp_path / "src"
    shutil.copytree(PKG, copy_src / "claview" / "a", ignore=shutil.ignore_patterns("__pycache__"))
    (copy_src / "claview" / "a" / "__init__.py").write_text("import claview.aflow\n", encoding="utf-8")
    # Empty stand-ins for the parent package and the old module beside the copy (one package claview on the path):
    # nothing old is executed.
    stand_in = copy_src / "claview"
    (stand_in / "aflow").mkdir()
    (stand_in / "__init__.py").write_text("", encoding="utf-8")
    (stand_in / "aflow" / "__init__.py").write_text("", encoding="utf-8")
    result = _run_child("import claview.a._guard as g\ng.install()\nprint('INSTALLED')\n",
                        [copy_src], tmp_path)
    assert result.returncode != 0 and "INSTALLED" not in result.stdout
    assert "already loaded" in result.stderr
