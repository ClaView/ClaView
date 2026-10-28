"""Axis-role table: the four layers of the user's draft, the target-value dictionary and the draft overrides.

Rule source: docs/inputs/axis-roles-20261016/지표축_나누기_초안_20261016.md (paths.AXIS_ROLES_DRAFT).

- Layer 1: axis name -> role, read from ``축이름_역할_초안.csv`` (target / basis / indicator / per-axis / human).
- Layer 2: generic axes (계정항목별 등). An axis is a target axis if half or more of its non-total values are in the
  target dictionary (computed here, ratio kept as evidence). The user's per-axis judgments
  (``일반축_축마다_판정.csv``) are linked to axis IDs and, when ``use_draft_csv`` is on, take precedence.
- Layer 3: values of indicator axes -> basis / total header / compound / target (exact dictionary hit or sibling
  majority) / indicator. Compound is only the "A-B" form and a (사용)/(원천) tail, never mere containment.
- Layer 4: pressed axes (a bundle of same-named children repeated under two or more differently named parents),
  detected here and listed by the user in ``눌린축_목록.csv``. Each level gets a role.

Draft overrides (``data/axis_role_overrides.csv``, from the EDA confirmation sheet) are applied when ``overrides`` is
on. Only the machine columns are read (``role``, ``level_roles``, ``value_role``, ``value_exceptions``; written by
``import_overrides`` from the free-text recommendation). A row with a filled ``decision_*`` column is approved and its
decision columns are used instead of the recommendation columns. Every row is reported (applied / unchanged /
not_applied + reason):
- ``role`` indicator / target / basis: the axis role. ``role`` pressed: a mixed axis; it becomes an indicator axis
  and its levels get ``level_roles`` (applied only if the axis has a pressed structure with that many levels).
- ``value_exceptions`` (only with ``role`` indicator / target / basis): ``name=type;name=type``. After the axis role,
  the values of that axis with those names get the named type (the recommendation's "뿌리 '수입' = 지표" inside the
  parentheses). If a named value is not in the axis, nothing of the row is applied to that table.
- ``level_roles`` without ``role``: level roles of the axis's pressed structure, any number of levels.
- ``value_role``: the dual-role values of that name in the listed tables (``표 ID``) only; a cut list is not applied.

The CSV files name tables by table ID and axes by axis name. They are linked to axis IDs here; a row that links to
no axis or to several axes gets no judgment and is listed in ``link_problems``. No table, case or question ID is
written in this module; the per-table facts live in the data files.

Rule helpers marked "Source:" are copied from scripts/eda_v3/roles.py (commit b118a64), outside the package
boundary, so that the computed layers can be compared with the EDA recount.
"""

from __future__ import annotations
import collections
import csv
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from .. import paths
from .loader import ITEM_OBJ_ID, Axis, Catalog, Item
from .text import bkey, clean_value, collapse, key, nfkc, strip_parens


# Source: scripts/eda_v3/roles.py:19 (commit b118a64); "지표" maps to indicator (EDA name: measure).
LAYER1_ROLE = {"대상": "target", "기준": "basis", "지표": "indicator", "축마다 판정": "generic", "확인": "human"}
CSV_ROLE = {"지표": "indicator", "대상": "target"}

# Source: scripts/eda_v3/roles.py:22 (commit b118a64). Totals: kept out of the target dictionary and the ratios.
_TOTAL = re.compile(r"^(?:합계|총계|소계|계|전체|총액|합|.+총계|.+합계)$")
# Source: scripts/eda_v3/roles.py:30 (commit b118a64). "2024년기준"
_BASIS_EXTRA = re.compile(r"\d{4}년")
# Source: scripts/eda_v3/roles.py:32 (commit b118a64), one change: after a Hangul (or closing parenthesis) and the
# hyphen a digit may follow, so the draft's own example "정기예금-2%미만" is the "A-B" form. "Non-MMF부문" (no Hangul
# next to the hyphen) stays one name. A value that merely contains a target word ("가계신용") is never compound.
_COMBINED = re.compile(r"[가-힣)]\s*-\s*[가-힣A-Za-z0-9(]|[가-힣A-Za-z)]\s*-\s*[가-힣(]|\((?:사용|원천)\)\s*$")
# Source: scripts/eda_v3/roles.py:33 (commit b118a64).
_AGE = re.compile(r"^\d{2}\s*(?:대|세)")
_TARGET_DICT_SUFFIX_DROP = tuple(json.loads(paths.ROLE_WORDS.read_text(encoding="utf-8"))
                                 ["target_dictionary_suffix_drop"])  # plan 11 R1b: words in a data file
_SUFFIX_DROP_LABEL = "loan/fund(" + "/".join("…" + w for w in _TARGET_DICT_SUFFIX_DROP) + ")"

TARGET_RATIO = 0.5


def base(name: str | None) -> str:
    """Comparison key of a value (source: scripts/eda_v3/roles.py:39-42, commit b118a64)."""
    return bkey(name)


def is_total_key(k: str) -> bool:
    # Source: scripts/eda_v3/roles.py:45-46 (commit b118a64).
    return bool(_TOTAL.match(k))


def basis_only(k: str, vocab: set[str]) -> bool:
    """True if the key is made only of basis words (source: scripts/eda_v3/roles.py:49-56, commit b118a64)."""
    if not k:
        return False
    rest = k
    for w in sorted(vocab, key=len, reverse=True):
        if w:
            rest = rest.replace(w, "")
    return _BASIS_EXTRA.sub("", rest) == ""


def is_combined(name: str | None) -> bool:
    # Source: scripts/eda_v3/roles.py:59-60 (commit b118a64), with the regex change noted at _COMBINED.
    return bool(_COMBINED.search(clean_value(name)))


def _is_header(item: Item, k: str, has_child: set[str]) -> bool:
    # Source: scripts/eda_v3/roles.py:146-147, 158 (commit b118a64).
    return is_total_key(k) or (item.item_id in has_child and (k.startswith("총") or k.startswith("합계")))


def read_basis_words(path: Path | None = None) -> list[str]:
    lines = Path(path or paths.BASIS_WORDS).read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def read_csv(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


@dataclass
class AxisRole:
    table_id: str
    obj_id: str
    axis_name: str | None
    layer1: str  # layer-1 role of the axis name (generic / human / unknown included)
    role: str  # final axis role: indicator / target / basis / human / unknown
    layer: int | None  # layer that decided the role (1 or 2)
    rule: str
    ratio: float = 0.0
    hit: int = 0
    n_nontotal: int = 0
    computed_role: str | None = None  # layer-2 ratio judgment (generic axes)
    csv_role: str | None = None  # user's per-axis judgment, when linked
    csv_source: str | None = None
    override: dict | None = None
    status: str = "draft"


@dataclass
class ValueRole:
    table_id: str
    obj_id: str
    item_id: str
    name: str | None
    type: str
    layer: int | None  # 0 = ITEM row, 1..4 = layer that decided the type
    rule: str
    axis_role: str
    status: str = "draft"
    override: dict | None = None
    pressed_level: str | None = None  # upper / lower (three or more levels: upper / middle... / lower)
    dual: bool = False  # exact dictionary hit whose siblings are mostly not targets (confirmation item 4)
    layer3_type: str | None = None  # layer-3 result before layers 4 and value overrides (indicator axes)
    layer3_rule: str | None = None

    @property
    def overridden(self) -> bool:
        return self.override is not None


@dataclass
class Pressed:
    table_id: str
    obj_id: str
    axis_name: str | None
    detected: bool  # found by the layer-4 rule here
    in_draft_csv: bool  # listed in 눌린축_목록.csv
    csv_source: str | None
    upper_ids: tuple[str, ...]
    lower_ids: tuple[str, ...]
    upper_ratio: float
    lower_ratio: float
    upper_role: str
    lower_role: str
    level_source: str = "computed"  # computed / override
    applied: bool = False
    note: str = ""
    override: dict | None = None
    # Level roles of an override (top level first) and, for three or more levels, the value IDs of each level
    # (the two-level reading uses upper_ids / lower_ids).
    level_roles: tuple[str, ...] = ()
    level_ids: tuple[tuple[str, ...], ...] = ()
    # Upper values given no level (a root total above the top level, three or more levels); they keep their type.
    outside_ids: tuple[str, ...] = ()


@dataclass
class _Structure:
    """Pressed structure of an axis: upper values (parents of the repeated bundle) and lower values (the bundle)."""

    upper: set[str]
    lower: set[str]
    upper_keys: set[str]
    lower_keys: set[str]
    in_bundle: Callable[[str], bool]  # item ID -> its name is one of the repeated (bundle) names
    detected: bool  # found by the layer-4 rule (else read from the pressed-axis CSV)


@dataclass
class TargetDictionary:
    """Target values taken from the layer-1 target axes (draft layer 2): key -> source axis names."""

    keys: dict[str, set[str]]
    dropped: collections.Counter
    age_added: set[str]

    def __contains__(self, k: str) -> bool:
        return k in self.keys

    def __len__(self) -> int:
        return len(self.keys)


@dataclass(frozen=True)
class Override:
    """One row of the overrides file, read from its machine columns (the free-text columns are kept for people).

    ``role`` is one axis role (indicator / target / basis) or ``pressed`` (a mixed axis: an indicator axis whose
    levels get ``level_roles``). ``level_roles`` lists the level roles from the top level down (any number of
    levels). ``value_role`` is the type of the named value in the listed tables. ``value_exceptions`` goes with an
    axis role: (value name, type) pairs given to those values of the axis after the axis role. The ``decision_*``
    columns have the same form; when any of them is filled the row is approved and the four decision columns replace
    the recommendation columns as a whole.
    """

    section: str
    table_ids: tuple[str, ...]
    truncated: bool
    subject: str
    recommended: str
    confidence: str
    evidence: str
    role: str
    level_roles: tuple[str, ...]
    value_role: str
    value_exceptions: tuple[tuple[str, str], ...]
    parse_note: str
    status: str
    decision_role: str
    decision_level_roles: tuple[str, ...]
    decision_value_role: str
    decision_value_exceptions: tuple[tuple[str, str], ...]
    source: str

    @property
    def decided(self) -> bool:
        return bool(self.decision_role or self.decision_level_roles or self.decision_value_role
                    or self.decision_value_exceptions)

    @property
    def effective_role(self) -> str:
        return self.decision_role if self.decided else self.role

    @property
    def effective_levels(self) -> tuple[str, ...]:
        return self.decision_level_roles if self.decided else self.level_roles

    @property
    def effective_value_role(self) -> str:
        return self.decision_value_role if self.decided else self.value_role

    @property
    def effective_value_exceptions(self) -> tuple[tuple[str, str], ...]:
        return self.decision_value_exceptions if self.decided else self.value_exceptions

    @property
    def effective(self) -> str:
        """Machine reading in one string (report and tags): "role=pressed; level_roles=indicator|target"."""
        exceptions = self.effective_value_exceptions
        parts = [f"role={self.effective_role}" if self.effective_role else "",
                 f"level_roles={LEVEL_SEP.join(self.effective_levels)}" if self.effective_levels else "",
                 f"value_role={self.effective_value_role}" if self.effective_value_role else "",
                 f"value_exceptions={format_exceptions(exceptions)}" if exceptions else ""]
        return "; ".join(p for p in parts if p)

    @property
    def effective_status(self) -> str:
        return "approved" if self.decided else (self.status.strip() or "draft")

    def tag(self) -> dict:
        return {"source": self.source, "section": self.section, "status": self.effective_status,
                "value": self.effective}


@dataclass
class AxisRoleTable:
    axes: dict[tuple[str, str], AxisRole]
    values: dict[tuple[str, str, str], ValueRole]
    pressed: list[Pressed]
    target_dict: TargetDictionary
    basis_vocab: set[str]
    links: dict[str, list[dict]]
    link_problems: list[dict]
    override_report: list[dict]
    settings: dict
    layer1_names: dict[str, str] = field(default_factory=dict)

    def value(self, table_id: str, obj_id: str, item_id: str) -> ValueRole | None:
        return self.values.get((table_id, obj_id, item_id))

    def axis(self, table_id: str, obj_id: str) -> AxisRole | None:
        return self.axes.get((table_id, obj_id))


OVERRIDE_FIELDS = ("구분", "표 ID", "축 이름 또는 값", "recommended", "confidence", "evidence", "role", "level_roles",
                   "value_role", "value_exceptions", "parse_note", "status", "decision_role", "decision_level_roles",
                   "decision_value_role", "decision_value_exceptions", "source_row")
# Machine columns: allowed tokens. LEVEL_SEP joins level roles from the top level down.
AXIS_OVERRIDE_ROLES = ("indicator", "target", "basis", "pressed")
LEVEL_ROLES = ("indicator", "target", "basis", "total_header", "compound")
VALUE_OVERRIDE_ROLES = LEVEL_ROLES
# Axis roles that may carry value exceptions (a pressed axis gets its value types from its levels).
EXCEPTION_AXIS_ROLES = ("indicator", "target", "basis")
LEVEL_SEP = "|"
EXCEPTION_SEP = ";"
TRUNCATED_MARK = "…"


def format_exceptions(pairs) -> str:
    """((name, type), ...) -> "name=type;name=type"."""
    return EXCEPTION_SEP.join(f"{name}={role}" for name, role in pairs)


def _exceptions(row: dict, field: str, where: str) -> tuple[tuple[str, str], ...]:
    """Read "name=type;name=type" (type one of VALUE_OVERRIDE_ROLES); bad text raises ValueError."""
    text = (row.get(field) or "").strip()
    if not text:
        return ()
    out = []
    for part in text.split(EXCEPTION_SEP):
        name, eq, role = part.strip().rpartition("=")
        name, role = name.strip(), role.strip()
        if not eq or not name or role not in VALUE_OVERRIDE_ROLES:
            raise ValueError(f"{where}: {field} {text!r} needs name=type pairs (type in {list(VALUE_OVERRIDE_ROLES)})"
                             f" joined by {EXCEPTION_SEP!r}")
        out.append((name, role))
    return tuple(out)


def _tokens(row: dict, field: str, allowed: tuple[str, ...], where: str, many: bool = False):
    text = (row.get(field) or "").strip()
    if not many:
        if text and text not in allowed:
            raise ValueError(f"{where}: {field} {text!r} not in {list(allowed)}")
        return text
    if not text:
        return ()
    parts = tuple(t.strip() for t in text.split(LEVEL_SEP))
    bad = [t for t in parts if t not in allowed]
    if bad or len(parts) < 2:
        raise ValueError(f"{where}: {field} {text!r} needs two or more of {list(allowed)} joined by {LEVEL_SEP!r}")
    return parts


def _check_kinds(role: str, levels: tuple, value_role: str, where: str, prefix: str = "",
                 exceptions: tuple = ()) -> None:
    """A row is one of: an axis role (with or without value exceptions), pressed + level roles, level roles alone,
    a value role (or nothing)."""
    if value_role and (role or levels):
        raise ValueError(f"{where}: {prefix}value_role cannot be combined with {prefix}role/{prefix}level_roles")
    if levels and role and role != "pressed":
        raise ValueError(f"{where}: {prefix}level_roles go with {prefix}role 'pressed' or with no {prefix}role")
    if exceptions and role not in EXCEPTION_AXIS_ROLES:
        raise ValueError(f"{where}: {prefix}value_exceptions go with {prefix}role in {list(EXCEPTION_AXIS_ROLES)}")


def load_overrides(path: Path | None = None) -> list[Override]:
    """Read the overrides file. Only the machine columns are read for the judgment; bad tokens raise ValueError."""
    path = Path(path or paths.AXIS_ROLE_OVERRIDES)
    out = []
    for n, row in enumerate(read_csv(path), start=2):
        where = f"{path.name}:{n}"
        missing = [f for f in OVERRIDE_FIELDS[:-1] if f not in row]
        if missing:
            raise ValueError(f"{where}: missing columns {missing}")
        ids_text = (row["표 ID"] or "").strip()
        truncated = ids_text.endswith(TRUNCATED_MARK)
        ids = tuple(t.strip() for t in ids_text.rstrip(TRUNCATED_MARK).split("/") if t.strip())
        section = (row["구분"] or "").strip()[:1]
        role = _tokens(row, "role", AXIS_OVERRIDE_ROLES, where)
        levels = _tokens(row, "level_roles", LEVEL_ROLES, where, many=True)
        value_role = _tokens(row, "value_role", VALUE_OVERRIDE_ROLES, where)
        d_role = _tokens(row, "decision_role", AXIS_OVERRIDE_ROLES, where)
        d_levels = _tokens(row, "decision_level_roles", LEVEL_ROLES, where, many=True)
        d_value = _tokens(row, "decision_value_role", VALUE_OVERRIDE_ROLES, where)
        exceptions = _exceptions(row, "value_exceptions", where)
        d_exceptions = _exceptions(row, "decision_value_exceptions", where)
        _check_kinds(role, levels, value_role, where, exceptions=exceptions)
        _check_kinds(d_role, d_levels, d_value, where, "decision_", d_exceptions)
        out.append(Override(section, ids, truncated, (row["축 이름 또는 값"] or "").strip(),
                            row["recommended"] or "", row["confidence"] or "", row["evidence"] or "",
                            role, levels, value_role, exceptions, (row["parse_note"] or "").strip(),
                            row["status"] or "draft", d_role, d_levels, d_value, d_exceptions,
                            (row.get("source_row") or "").strip() or where))
    return out


def _link(catalog: Catalog, table_id: str, axis_name: str) -> list[Axis]:
    table = catalog.tables.get(table_id.strip())
    return table.axes_named(axis_name) if table else []


def _link_rows(catalog: Catalog, rows: list[dict], file_label: str, kind: str, problems: list[dict]) -> list[dict]:
    out = []
    for n, row in enumerate(rows, start=2):
        tid, name = (row.get("표 ID") or "").strip(), (row.get("축 이름") or "").strip()
        found = _link(catalog, tid, name)
        entry = {"kind": kind, "source": f"{file_label}:{n}", "table_id": tid, "axis_name": name,
                 "obj_ids": [ax.obj_id for ax in found], "status": "draft", "row": row}
        out.append(entry)
        if len(found) != 1:
            problems.append({"kind": kind, "source": entry["source"], "table_id": tid, "axis_name": name,
                             "found": len(found)})
    return out


class _Builder:
    def __init__(self, catalog: Catalog, *, use_draft_csv: bool, overrides: bool, layer1_path, generic_path,
                 collapsed_path, overrides_path, basis_words_path):
        self.catalog = catalog
        self.use_draft_csv = use_draft_csv
        self.use_overrides = overrides
        self.layer1_rows = read_csv(Path(layer1_path or paths.AXIS_NAME_ROLES))
        self.generic_rows = read_csv(Path(generic_path or paths.GENERIC_AXIS_ROLES))
        self.collapsed_rows = read_csv(Path(collapsed_path or paths.COLLAPSED_AXES))
        self.overrides = load_overrides(overrides_path) if overrides else []
        self.basis_words = read_basis_words(basis_words_path)
        self.l1 = {collapse(r["축 이름"]): LAYER1_ROLE.get((r.get("역할 초안") or "").strip(), "unknown")
                   for r in self.layer1_rows}
        self.axes: dict[tuple[str, str], AxisRole] = {}
        self.values: dict[tuple[str, str, str], ValueRole] = {}
        self.pressed: list[Pressed] = []
        self.report: list[dict] = []
        self.problems: list[dict] = []
        self.links = {
            "generic_csv": _link_rows(catalog, self.generic_rows, Path(generic_path or paths.GENERIC_AXIS_ROLES).name,
                                      "generic_csv", self.problems),
            "collapsed_csv": _link_rows(catalog, self.collapsed_rows,
                                        Path(collapsed_path or paths.COLLAPSED_AXES).name, "collapsed_csv",
                                        self.problems),
        }
        self.collapsed_by_axis = {(e["table_id"], e["obj_ids"][0]): e for e in self.links["collapsed_csv"]
                                  if len(e["obj_ids"]) == 1}
        # (override source, table ID) -> axis role before, for role=pressed rows that passed the structure check
        self.pressed_rows: dict[tuple[str, str], str] = {}
        # axis rows with value exceptions, applied (and reported) after layers 3 and 4:
        # (override, table ID, obj ID, axis role before, [(item IDs, type), ...])
        self.exception_rows: list[tuple[Override, str, str, str, list[tuple[list[str], str]]]] = []

    # ---- shared
    def named_axes(self):
        for axis in self.catalog.axes():
            if axis.named:
                yield axis

    def layer1_of(self, axis: Axis) -> str:
        return self.l1.get(collapse(axis.name), "unknown")

    def _report(self, ov: Override, outcome: str, reason: str = "", **extra) -> None:
        self.report.append({"source": ov.source, "section": ov.section, "table_ids": list(ov.table_ids),
                            "subject": ov.subject, "value": ov.effective, "status": ov.effective_status,
                            "outcome": outcome, "reason": reason, **extra})

    # ---- target dictionary (draft layer 2), source: scripts/eda_v3/roles.py:85-112 (commit b118a64)
    def build_target_dict(self) -> None:
        src: dict[str, set[str]] = collections.defaultdict(set)
        dropped: collections.Counter = collections.Counter()
        for axis in self.named_axes():
            if self.layer1_of(axis) != "target":
                continue
            for it in axis.items:
                k = base(it.name)
                if not k:
                    continue
                if is_total_key(k):
                    dropped["total"] += 1
                elif k.startswith("기타"):
                    dropped["other(기타…)"] += 1
                elif k.endswith(_TARGET_DICT_SUFFIX_DROP):
                    dropped[_SUFFIX_DROP_LABEL] += 1
                else:
                    src[k].add(axis.name)
        age_added: set[str] = set()
        for axis in self.named_axes():
            for it in axis.items:
                if _AGE.match(clean_value(it.name)):
                    k = base(it.name)
                    if k not in src:
                        age_added.add(k)
                    src[k].add("연령대(추가)")
        self.target_dict = TargetDictionary(dict(src), dropped, age_added)

    def build_basis_vocab(self) -> None:
        # Source: scripts/eda_v3/roles.py:114-118 and 52 (commit b118a64).
        vocab = {key(w) for w in self.basis_words}
        for axis in self.named_axes():
            if self.layer1_of(axis) == "basis":
                vocab |= {k for it in axis.items if (k := base(it.name)) and not is_total_key(k)}
        self.basis_vocab = vocab - {""}

    def target_ratio(self, items) -> tuple[float, int, int]:
        # Source: scripts/eda_v3/roles.py:123-127 (commit b118a64).
        keys = [k for it in items if (k := base(it.name)) and not is_total_key(k)]
        hit = sum(1 for k in keys if k in self.target_dict)
        return (hit / len(keys) if keys else 0.0), hit, len(keys)

    # ---- layers 1 and 2
    def judge_axes(self) -> None:
        csv_by_axis = {}
        for entry in self.links["generic_csv"]:
            if len(entry["obj_ids"]) == 1:
                csv_by_axis[(entry["table_id"], entry["obj_ids"][0])] = entry
        for axis in self.catalog.axes():
            k = (axis.table_id, axis.obj_id)
            if not axis.named:
                self.axes[k] = AxisRole(axis.table_id, axis.obj_id, None, "unknown", "unknown", None, "unnamed_axis")
                continue
            l1 = self.layer1_of(axis)
            ratio, hit, n = self.target_ratio(axis.items)
            ar = AxisRole(axis.table_id, axis.obj_id, axis.name, l1, l1, 1, "axis_name", ratio, hit, n)
            entry = csv_by_axis.get(k)
            if entry:
                ar.csv_role = CSV_ROLE.get((entry["row"].get("역할 판정") or "").strip())
                ar.csv_source = entry["source"]
            if l1 == "generic":
                ar.computed_role = "target" if ratio >= TARGET_RATIO else "indicator"
                ar.layer = 2
                if self.use_draft_csv and ar.csv_role:
                    ar.role, ar.rule = ar.csv_role, "draft_csv"
                else:
                    ar.role, ar.rule = ar.computed_role, "target_ratio"
            elif l1 == "human":
                ar.rule = "axis_name_pending"
            elif l1 == "unknown":
                ar.rule = "axis_name_not_in_table"
            self.axes[k] = ar

    def apply_axis_overrides(self) -> None:
        """Axis rows: one axis role, or ``pressed`` (made an indicator axis here, its levels placed in layer 4).

        Value rows and level-only rows are left to layer 4 and the value overrides. A pressed row is applied only if
        the axis has a pressed structure (detected, or listed in the pressed-axis CSV) whose levels match the row's
        level roles; otherwise nothing of it is applied.
        """
        for ov in self.overrides:
            role = ov.effective_role
            if ov.effective_value_role or (not role and ov.effective_levels):
                continue
            if not role:
                note = f" ({ov.parse_note})" if ov.parse_note else ""
                self._report(ov, "not_applied", "no machine-readable role" + note)
                continue
            for tid in ov.table_ids:
                found = _link(self.catalog, tid, ov.subject)
                if len(found) != 1:
                    self.problems.append({"kind": "override", "source": ov.source, "table_id": tid,
                                          "axis_name": ov.subject, "found": len(found)})
                    self._report(ov, "not_applied", f"axis link found {len(found)}", table_id=tid)
                    continue
                ar = self.axes[(tid, found[0].obj_id)]
                before = ar.role
                exceptions = ov.effective_value_exceptions if role in EXCEPTION_AXIS_ROLES else ()
                placed = []
                for name, value_type in exceptions:
                    ids = [it.item_id for it in found[0].items if it.named and base(it.name) == base(name)]
                    placed.append((ids, value_type))
                missing = [name for (name, _t), (ids, _v) in zip(exceptions, placed) if not ids]
                if missing:
                    self._report(ov, "not_applied", f"value exception not in the axis: {', '.join(missing)}; "
                                 f"axis role left {before}", table_id=tid)
                    continue
                if role == "pressed":
                    why = self._pressed_check(found[0], ov)
                    if why:
                        self._report(ov, "not_applied", f"{why}; axis role left {before}", table_id=tid)
                        continue
                    ar.role, ar.rule, ar.override, ar.status = ("indicator", "override_pressed", ov.tag(),
                                                                ov.effective_status)
                    self.pressed_rows[(ov.source, tid)] = before  # reported with its levels in judge_pressed
                    continue
                ar.role, ar.rule, ar.override, ar.status = role, "override", ov.tag(), ov.effective_status
                if placed:
                    self.exception_rows.append((ov, tid, found[0].obj_id, before, placed))
                    continue  # reported with its value exceptions in apply_value_exceptions
                self._report(ov, "applied" if before != role else "unchanged", f"{before} -> {role}", table_id=tid)

    def _pressed_check(self, axis: Axis, ov: Override) -> str:
        """Why a role=pressed row cannot be applied to this axis ("" = it can)."""
        levels = ov.effective_levels
        if not levels:
            return "pressed axis without level roles" + (f" ({ov.parse_note})" if ov.parse_note else "")
        entry = self.collapsed_by_axis.get((axis.table_id, axis.obj_id)) if self.use_draft_csv else None
        struct = self._structure(axis, entry)
        if struct is None:
            return "no pressed structure for this axis (not detected, not in the pressed-axis CSV)"
        _placed, why = self._place(axis, struct, levels)
        return why

    # ---- layer 3
    def classify_values(self) -> None:
        for table in self.catalog.tables.values():
            for it in table.item_rows:
                self.values[(it.table_id, ITEM_OBJ_ID, it.item_id)] = ValueRole(
                    it.table_id, ITEM_OBJ_ID, it.item_id, it.name, "series_name", 0, "item_row", "item")
        for axis in self.catalog.axes():
            ar = self.axes[(axis.table_id, axis.obj_id)]
            by_parent = axis.children()
            has_child = {it.parent_id for it in axis.items if it.parent_id}
            for it in axis.items:
                k = (axis.table_id, axis.obj_id, it.item_id)
                vr = ValueRole(axis.table_id, axis.obj_id, it.item_id, it.name, "unknown", ar.layer, "", ar.role,
                               status=ar.status, override=ar.override)
                if not it.named:
                    vr.rule, vr.status, vr.override = "unnamed_value", "draft", None
                elif ar.role == "indicator":
                    vr.layer, vr.status, vr.override = 3, "draft", None
                    vr.type, vr.rule, vr.dual = self._value_rule(it, by_parent, has_child)
                    vr.layer3_type, vr.layer3_rule = vr.type, vr.rule
                elif ar.role == "target":
                    head = _is_header(it, base(it.name), has_child)
                    vr.type, vr.rule = ("total_header", "target_axis_total") if head else ("target", "target_axis")
                elif ar.role == "basis":
                    vr.type, vr.rule = "basis", "basis_axis"
                else:
                    vr.rule = "axis_role_pending" if ar.role == "human" else "axis_role_unknown"
                self.values[k] = vr

    def _value_rule(self, it: Item, by_parent, has_child) -> tuple[str, str, bool]:
        # Source: scripts/eda_v3/roles.py:153-168 (commit b118a64).
        k = base(it.name)
        if basis_only(k, self.basis_vocab):
            return "basis", "basis_words", False
        if _is_header(it, k, has_child):
            return "total_header", "total_header", False
        if is_combined(it.name):
            return "compound", "compound_form", False
        siblings = by_parent.get(it.parent_id, [])
        ratio, _hit, n = self.target_ratio(siblings)
        if k in self.target_dict:
            return "target", "target_exact", n >= 2 and ratio < TARGET_RATIO
        if n >= 2 and ratio >= TARGET_RATIO:
            return "target", "target_sibling", False
        return "indicator", "indicator_default", False

    # ---- layer 4
    def _detect(self, axis: Axis):
        # Source: scripts/eda_v3/roles.py:174-205 (commit b118a64), returning item IDs per level.
        names = {it.item_id: base(it.name) for it in axis.items}
        kids: dict[str, set[str]] = collections.defaultdict(set)
        for it in axis.items:
            p = it.parent_id
            if p and p in names and names[it.item_id]:
                kids[p].add(names[it.item_id])
        parents_of: dict[str, set[str]] = collections.defaultdict(set)
        for p, ks in kids.items():
            for k in ks:
                parents_of[k].add(names[p])
        repeated = {k for k, ps in parents_of.items() if len(ps) >= 2}
        if len(repeated) < 2:
            return None
        plist = [p for p in kids if len(kids[p] & repeated) >= 2]
        pairs = [(a, b) for i, a in enumerate(plist) for b in plist[i + 1:]
                 if names[a] != names[b] and len(kids[a] & kids[b] & repeated) >= 2]
        if not pairs:
            return None
        bundle = set().union(*(kids[a] & kids[b] & repeated for a, b in pairs))
        upper = {p for ab in pairs for p in ab}
        lower = {it.item_id for it in axis.items if it.parent_id in upper and names[it.item_id] in bundle} - upper
        return upper, lower, {names[p] for p in upper}, bundle

    def _from_csv(self, axis: Axis, row: dict):
        def keys(text):
            return {key(n) for n in (text or "").split(" / ") if key(n)}

        parent_keys, child_keys = keys(row.get("그 부모들")), keys(row.get("반복되는 자식 이름"))
        upper = {it.item_id for it in axis.items if _item_keys(it) & parent_keys}
        lower = {it.item_id for it in axis.items if it.parent_id in upper and _item_keys(it) & child_keys} - upper
        return upper, lower, parent_keys, child_keys

    def _structure(self, axis: Axis, entry: dict | None) -> _Structure | None:
        """Pressed structure: the layer-4 rule first, else the pressed-axis CSV row; None if neither."""
        items = {it.item_id: it for it in axis.items}
        detected = self._detect(axis)
        if detected is not None:
            upper, lower, upper_keys, bundle = detected
            return _Structure(upper, lower, upper_keys, bundle, lambda i: base(items[i].name) in bundle, True)
        if entry is None:
            return None
        upper, lower, parent_keys, child_keys = self._from_csv(axis, entry["row"])
        return _Structure(upper, lower, parent_keys, child_keys, lambda i: bool(_item_keys(items[i]) & child_keys),
                          False)

    def _place(self, axis: Axis, struct: _Structure, levels: tuple[str, ...]):
        """Value IDs of each level, top level first, or (None, reason).

        Two levels: the upper and lower values of the structure. Three or more, placed by the hierarchy: the lower
        values are the last level; an upper value whose name is repeated (bundle) is one level below each repeated
        upper value above it (a chain of repeated bundles); the upper values whose names are not repeated (the
        sectors the bundles hang from) are the top level. When those non-repeated values have one common root among
        them (a single one with no non-repeated upper value above it, over two or more), that root is a total above
        the sectors: it gets no level and keeps its layer-3 type, unless the override names one more level than the
        structure has without it, in which case the root is that extra top level. The number of levels is checked
        after this placement: it must equal the number of level roles, every level non-empty.
        """
        if not struct.upper or not struct.lower:
            return None, "level values not found in the axis"
        if len(levels) == 2:
            return (tuple(sorted(struct.upper)), tuple(sorted(struct.lower))), ""
        parent = {it.item_id: it.parent_id for it in axis.items}

        def ancestors(i):
            seen, p = {i}, parent.get(i)
            while p is not None and p in parent and p not in seen:
                seen.add(p)
                yield p
                p = parent[p]

        ranks = {}
        for i in struct.upper:
            if not struct.in_bundle(i):
                ranks[i] = 0
                continue
            ranks[i] = 1 + sum(1 for p in ancestors(i) if p in struct.upper and struct.in_bundle(p))
        sectors = {i for i, r in ranks.items() if r == 0}
        tops = [i for i in sectors if not any(p in sectors for p in ancestors(i))]
        root = tops[0] if len(tops) == 1 and len(sectors) >= 2 else None
        found = max(ranks.values()) + 2
        groups = [tuple(sorted(i for i, r in ranks.items() if r == k and i != root)) for k in range(found - 1)]
        groups.append(tuple(sorted(struct.lower)))
        if root is not None and len(levels) == found + 1:
            groups, found = [(root,), *groups], found + 1
        if found != len(levels) or not all(groups):
            sizes = "/".join(str(len(g)) for g in groups)
            return None, f"the pressed structure has {found} levels (values {sizes}), the override {len(levels)}"
        return tuple(groups), ""

    def judge_pressed(self) -> None:
        level_overrides = collections.defaultdict(list)
        for ov in self.overrides:  # empty when overrides are off
            if not ov.effective_levels or ov.effective_value_role:
                continue
            for tid in ov.table_ids:
                if ov.effective_role == "" or (ov.source, tid) in self.pressed_rows:
                    level_overrides[(tid, collapse(ov.subject))].append(ov)

        used: set[tuple[str, str]] = set()
        for axis in self.named_axes():
            ar = self.axes[(axis.table_id, axis.obj_id)]
            entry = self.collapsed_by_axis.get((axis.table_id, axis.obj_id)) if self.use_draft_csv else None
            struct = self._structure(axis, entry) if ar.role == "indicator" else None
            if struct is None and entry is None:
                continue
            if ar.role != "indicator":
                self.pressed.append(Pressed(axis.table_id, axis.obj_id, axis.name, False, True, entry["source"],
                                            (), (), 0.0, 0.0, "indicator", "indicator",
                                            note=f"axis role is {ar.role}, not an indicator axis"))
                continue
            upper, lower, upper_keys, lower_keys = struct.upper, struct.lower, struct.upper_keys, struct.lower_keys
            up_t = sum(1 for k in upper_keys if k in self.target_dict) / len(upper_keys) if upper_keys else 0.0
            lo_t = sum(1 for k in lower_keys if k in self.target_dict) / len(lower_keys) if lower_keys else 0.0
            up_b = sum(1 for k in upper_keys if basis_only(k, self.basis_vocab)) / len(upper_keys) if upper_keys else 0.0
            lo_b = sum(1 for k in lower_keys if basis_only(k, self.basis_vocab)) / len(lower_keys) if lower_keys else 0.0
            pr = Pressed(axis.table_id, axis.obj_id, axis.name, struct.detected, entry is not None,
                         entry["source"] if entry else None, tuple(sorted(upper)), tuple(sorted(lower)),
                         round(up_t, 2), round(lo_t, 2), _level_role(up_t, up_b), _level_role(lo_t, lo_b))
            if not upper or not lower:
                pr.note = "level values not found in the axis"
            pending = []
            for ov in level_overrides.get((axis.table_id, collapse(axis.name)), []):
                used.add((ov.source, axis.table_id))
                if pr.note:
                    self._report(ov, "not_applied", pr.note, table_id=axis.table_id)
                    continue
                levels = ov.effective_levels
                placed, why = self._place(axis, struct, levels)
                if placed is None:
                    self._report(ov, "not_applied", why, table_id=axis.table_id)
                    continue
                pending.append((ov, (pr.upper_role, pr.lower_role), levels))
                pr.level_roles, pr.level_ids = levels, (placed if len(levels) > 2 else ())
                pr.outside_ids = (tuple(sorted(struct.upper - {i for g in placed for i in g}))
                                  if len(levels) > 2 else ())
                pr.upper_role, pr.lower_role = levels[0], levels[-1]
                pr.level_source, pr.override = "override", ov.tag()
            changed = self._apply_levels(pr) if not pr.note else 0
            for ov, before, levels in pending:
                axis_before = self.pressed_rows.get((ov.source, axis.table_id))
                reason = f"levels {'/'.join(before)} -> {'/'.join(levels)}; {changed} values changed"
                if pr.outside_ids:
                    reason += f"; {len(pr.outside_ids)} root values above the top level keep their type"
                if axis_before is not None:
                    reason = f"axis {axis_before} -> indicator (pressed); " + reason
                moved = changed or (axis_before is not None and axis_before != "indicator")
                self._report(ov, "applied" if moved else "unchanged", reason, table_id=axis.table_id)
            self.pressed.append(pr)
        for ov in self.overrides:
            if not ov.effective_levels or ov.effective_value_role:
                continue
            for tid in ov.table_ids:
                if (ov.source, tid) in used:
                    continue
                if ov.effective_role == "pressed":
                    if (ov.source, tid) in self.pressed_rows:  # structure seen before layer 3 but not here
                        self._report(ov, "not_applied", "pressed structure not found after layer 3", table_id=tid)
                    continue
                if ov.effective_role:
                    continue
                found = _link(self.catalog, tid, ov.subject)
                if len(found) != 1:
                    self.problems.append({"kind": "override", "source": ov.source, "table_id": tid,
                                          "axis_name": ov.subject, "found": len(found)})
                    self._report(ov, "not_applied", f"axis link found {len(found)}", table_id=tid)
                    continue
                role = self.axes[(tid, found[0].obj_id)].role
                if role != "indicator" and all(level == role for level in ov.effective_levels):
                    why = f"duplicate: the axis is a {role} axis and every level role is {role}"
                else:
                    why = f"no pressed structure for this axis (axis role {role})"
                self._report(ov, "not_applied", why, table_id=tid)

    def _apply_levels(self, pr: Pressed) -> int:
        """Give the level roles to the level values; returns how many value types changed."""
        pr.applied = True
        if pr.level_ids:
            levels = list(zip(_level_names(len(pr.level_ids)), pr.level_ids, pr.level_roles))
        else:
            levels = [("upper", pr.upper_ids, pr.upper_role), ("lower", pr.lower_ids, pr.lower_role)]
        changed = 0
        for level, ids, role in levels:
            for item_id in ids:
                vr = self.values[(pr.table_id, pr.obj_id, item_id)]
                vr.pressed_level = level
                if vr.type in ("basis", "total_header", "compound"):
                    continue  # name-form facts of layer 3 are kept
                if pr.level_source == "computed" and role not in ("target", "basis"):
                    continue  # computed default (indicator) leaves the layer-3 type
                changed += vr.type != role
                vr.type, vr.layer, vr.rule = role, 4, f"pressed_{level}"
                vr.override = pr.override
                vr.status = pr.override["status"] if pr.override else "draft"
        return changed

    # ---- value exceptions of axis rows
    def apply_value_exceptions(self) -> None:
        """The named values of an overridden axis get their exception type (after layers 3 and 4)."""
        for ov, tid, obj_id, before, placed in self.exception_rows:
            role = self.axes[(tid, obj_id)].role
            values = changed = 0
            for ids, value_type in placed:
                for item_id in ids:
                    vr = self.values[(tid, obj_id, item_id)]
                    values += 1
                    changed += vr.type != value_type
                    vr.type, vr.rule, vr.override, vr.status = (value_type, "override_value_exception", ov.tag(),
                                                                ov.effective_status)
            moved = before != role or changed
            self._report(ov, "applied" if moved else "unchanged",
                         f"{before} -> {role}; value exceptions: {values} values, {changed} changed", table_id=tid,
                         exception_values=values, exception_changed=changed)

    # ---- value overrides (confirmation item 4)
    def apply_value_overrides(self) -> None:
        """Value rows: the dual-role values of that name in the listed tables only."""
        by_table: dict[str, list[ValueRole]] = collections.defaultdict(list)
        for vr in self.values.values():
            by_table[vr.table_id].append(vr)
        for ov in self.overrides:
            role = ov.effective_value_role
            if not role:
                continue
            if ov.truncated:
                self._report(ov, "not_applied", f"table list is cut ({TRUNCATED_MARK}); the full list is needed")
                continue
            k = base(ov.subject)
            named = [vr for tid in ov.table_ids for vr in by_table.get(tid, []) if base(vr.name) == k]
            matched = [vr for vr in named if vr.dual]
            if not matched:
                if named and all(vr.type == role for vr in named):
                    self._report(ov, "unchanged", f"already {role} in the listed tables (no dual-role value left)")
                else:
                    self._report(ov, "not_applied", "no dual-role value with this name in the listed tables")
                continue
            tables = sorted({vr.table_id for vr in matched})
            listed_missing = sorted(set(ov.table_ids) - set(tables))
            changed = 0
            for vr in matched:
                if vr.type != role:
                    changed += 1
                vr.type, vr.rule, vr.override, vr.status = role, "override_value", ov.tag(), ov.effective_status
            self._report(ov, "applied" if changed else "unchanged", f"{len(matched)} values in {len(tables)} tables",
                         values=len(matched), changed=changed, listed_tables_not_matched=listed_missing)

    def build(self) -> AxisRoleTable:
        self.build_target_dict()
        self.build_basis_vocab()
        self.judge_axes()
        if self.use_overrides:
            self.apply_axis_overrides()
        self.classify_values()
        self.judge_pressed()
        if self.use_overrides:
            self.apply_value_exceptions()
            self.apply_value_overrides()
        return AxisRoleTable(
            axes=self.axes, values=self.values, pressed=self.pressed, target_dict=self.target_dict,
            basis_vocab=self.basis_vocab, links=self.links, link_problems=self.problems, override_report=self.report,
            settings={"use_draft_csv": self.use_draft_csv, "overrides": self.use_overrides},
            layer1_names=dict(self.l1),
        )


def _item_keys(it: Item) -> set[str]:
    """Keys a value is matched by against the pressed-axis CSV names."""
    return {key(strip_parens(nfkc(it.name or ""))), base(it.name)} - {""}


def _level_names(n: int) -> list[str]:
    """Level labels top first: upper, middle (or middle1, middle2, ...), lower."""
    middle = ["middle"] if n == 3 else [f"middle{k}" for k in range(1, n - 1)]
    return ["upper", *middle, "lower"]


def _level_role(target_ratio: float, basis_ratio: float) -> str:
    # Source: scripts/eda_v3/roles.py:208-209 (commit b118a64).
    return "target" if target_ratio >= TARGET_RATIO else ("basis" if basis_ratio >= TARGET_RATIO else "indicator")


def build_axis_roles(catalog: Catalog, *, use_draft_csv: bool = True, overrides: bool = True,
                     layer1_path: Path | None = None, generic_path: Path | None = None,
                     collapsed_path: Path | None = None, overrides_path: Path | None = None,
                     basis_words_path: Path | None = None) -> AxisRoleTable:
    """Build the axis-role table.

    use_draft_csv: use the user's per-axis judgments and pressed-axis list (off = computed rules only, which is the
    EDA recount). overrides: apply ``data/axis_role_overrides.csv`` (machine columns; draft rows, a filled
    ``decision_*`` column = approved).
    """
    return _Builder(catalog, use_draft_csv=use_draft_csv, overrides=overrides, layer1_path=layer1_path,
                    generic_path=generic_path, collapsed_path=collapsed_path, overrides_path=overrides_path,
                    basis_words_path=basis_words_path).build()
