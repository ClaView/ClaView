"""Carry the EDA confirmation sheet's recommended decisions into the package data file as draft overrides.

    PYTHONPATH=src PYTHONHASHSEED=0 .venv/Scripts/python -B -X utf8 -m claview_a_legacy.catalog.import_overrides

Input: paths.CONFIRM_SHEET_RECOMMENDED (runs/eda_v3/2_axis_roles/confirm_sheet_recommended.csv, outside git).
Output: paths.AXIS_ROLE_OVERRIDES (modules/a/claview_a_legacy/data/axis_role_overrides.csv), columns ``roles.OVERRIDE_FIELDS``:

- kept for people as written in the sheet: 구분, 축 이름 또는 값, recommended, confidence, evidence;
- machine columns read by the loader, filled here from the free-text ``recommended``: ``role`` (one axis role
  indicator / target / basis, or ``pressed`` for a mixed axis), ``level_roles`` (level roles from the top level down
  joined by ``|``, any number of levels), ``value_role`` (type of a dual-role value), ``value_exceptions`` (with an
  axis role: the "'값' = 역할" exceptions inside the parentheses, ``name=type;name=type``). What cannot be read is
  left blank and explained in ``parse_note``; a role-like exception that cannot be read leaves the whole row blank;
- ``표 ID``: the sheet's list; a list cut with "…" is rebuilt in full (sorted) from the sheet's own evidence list,
  or else from the catalog (the dual-role values of that name, rules only), and kept cut when neither gives a list of
  the size the sheet states (the loader does not apply a cut list);
- ``status`` (= draft) and the user's ``decision_role`` / ``decision_level_roles`` / ``decision_value_role`` /
  ``decision_value_exceptions`` (same form as the machine columns). Decisions already in the output file are kept for
  the row with the same 구분, 축 이름 또는 값 and whole table-ID set, so the file can be regenerated without losing
  them; a decision that finds no such row is warned about and kept in paths.ORPHAN_DECISIONS
  (runs/v3_stretch1/orphan_decisions.csv). A free-text ``decision`` in the sheet (or in an output file of the old
  format) is read like a recommendation into the decision columns.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

from .. import paths
from .roles import (LEVEL_SEP, OVERRIDE_FIELDS, TRUNCATED_MARK, VALUE_OVERRIDE_ROLES, base, build_axis_roles,
                    format_exceptions, read_csv)
from .text import nfkc

# ---------------------------------------------------------------- reading the free-text recommendation

_ROLE_WORDS = {"합계 머리": "total_header", "지표": "indicator", "대상": "target", "기준": "basis"}
_COMPOUND = re.compile(r"(?:지표|대상|기준)(?:\s*\+\s*(?:지표|대상|기준))+\s*결합")
_LEVEL_WORDS = ("맨 위", "위", "가운데", "아래")  # top to bottom
_LEVEL = re.compile(r"^(맨\s*위|위|가운데|아래)\s+(.+)$", re.S)
_AXIS_ROLES = ("indicator", "target", "basis")


def _split_top(text: str, sep: str = "/") -> list[str]:
    """Split at ``sep`` outside parentheses and brackets."""
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _outside_groups(text: str) -> str:
    """The text with every parenthesised group removed."""
    out, depth = [], 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _first_group(text: str) -> str | None:
    """Content of the first top-level parenthesised group."""
    start, depth = None, 0
    for i, ch in enumerate(text):
        if ch == "(":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                return text[start:i]
    return None


def _groups(text: str) -> list[str]:
    """Contents of every top-level parenthesised group."""
    out, start, depth = [], None, 0
    for i, ch in enumerate(text):
        if ch == "(":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                out.append(text[start:i])
    return out


def _head(text: str) -> str:
    """Role phrase of a recommendation: outside parentheses, before a colon, without a trailing "축"."""
    head = _outside_groups(nfkc(text)).split(":", 1)[0].strip()
    return re.sub(r"\s*축$", "", head).strip()


# ---------------------------------------------------------------- value exceptions inside the parentheses

# A clause that names a role ("뿌리 '수입' = 지표", "총부가가치·국내총생산은 지표/합계 머리").
_ROLE_MENTION = re.compile(r"지표|대상|기준|합계\s*머리|결합")
# subject, then "=" or a topic particle (은/는) closing a word, then the role phrase
_EXCEPTION = re.compile(r"^(?P<subject>.+?)\s*(?:=|(?<=\S)(?:은|는)(?=\s))\s*(?P<role>.+)$", re.S)
_QUOTED = re.compile(r"['\"‘’“”]([^'\"‘’“”]+)['\"‘’“”]")
_GENERIC_SUBJECT = "값"  # "값 = …" describes all values of the axis, not an exception


def parse_exceptions(text: str) -> tuple[list[tuple[str, str]], str]:
    """Value exceptions inside the parentheses of an axis-role recommendation -> ([(value name, role)], note).

    Each group is split at ";" into clauses. A clause without a role word is a description and is skipped, and so is
    a clause about all values ("값 = …"). Any other clause that names a role must read as "<values> = <role>" or
    "<values>은/는 <role>": the values are the quoted names, or else the last word split at "·" (a leading "뿌리",
    "끝의" … is dropped this way); the role is one value role, or "A/B" (the first role is taken and the note says
    so). A clause that names a role but does not read this way makes the note start with "값 예외를 읽지 못함" and
    no exception is returned: the caller then does not apply the row.
    """
    pairs: list[tuple[str, str]] = []
    notes: list[str] = []
    for group in _groups(nfkc(text)):
        for clause in _split_top(group, ";"):
            if not _ROLE_MENTION.search(clause):
                continue
            m = _EXCEPTION.match(clause)
            if m and m.group("subject").strip() == _GENERIC_SUBJECT:
                continue
            if not m:
                return [], f"값 예외를 읽지 못함: '{clause}'(값 = 역할 꼴이 아님)"
            subject, role_text = m.group("subject").strip(), m.group("role").strip()
            quoted = _QUOTED.findall(subject)
            names = [q.strip() for q in quoted] if quoted else subject.split()[-1].split("·")
            names = [n.strip() for n in names if n.strip()]
            choices = [parse_role(r) for r in _split_top(role_text)]
            if not names or not choices or any(c not in VALUE_OVERRIDE_ROLES for c in choices):
                return [], f"값 예외를 읽지 못함: '{clause}'(값 이름이나 역할을 읽지 못함)"
            if len(choices) > 1:
                notes.append(f"값 예외 '{clause}': 역할 '{role_text}' 중 첫 역할({choices[0]})로 읽음")
            pairs += [(n, choices[0]) for n in names]
    return pairs, "; ".join(notes)


def parse_role(text: str) -> str | None:
    """The one role a recommendation names: "대상 축(...)" -> target, "기준+지표 결합(...)" -> compound.

    Strict: the phrase outside parentheses (before a colon) must be a role word and nothing else, so
    "대상(...)과 지표가 섞임" or "눌린 축(...)" give None.
    """
    head = _head(text)
    if _COMPOUND.fullmatch(head):
        return "compound"
    return _ROLE_WORDS.get(head)


def parse_levels(text: str) -> tuple[str, ...] | None:
    """"위 R1 / 아래 R2", "맨 위 R1 / 가운데 R2 / 아래 R3" ... -> (R1, R2, ...), top level first.

    Level words run top to bottom (맨 위, 위, 가운데, 아래), start at 맨 위 or 위 and end at 아래; each level names
    exactly one role. Anything else (a level that mixes roles, a part without a level word) -> None.
    """
    parts = _split_top(nfkc(text))
    if len(parts) < 2:
        return None
    ranks, roles = [], []
    for part in parts:
        m = _LEVEL.match(part)
        if not m:
            return None
        role = parse_role(m.group(2))
        if role is None:
            return None
        ranks.append(_LEVEL_WORDS.index(re.sub(r"\s+", " ", m.group(1))))
        roles.append(role)
    if ranks != sorted(set(ranks)) or ranks[0] > 1 or ranks[-1] != len(_LEVEL_WORDS) - 1:
        return None
    return tuple(roles)


def machine_columns(section: str, text: str) -> dict:
    """role / level_roles / value_role / parse_note for one recommendation (or decision) of a sheet section.

    Sections (구분): 1 axis names, 2 boundary axes, 5 component items -> one axis role, or a pressed axis
    ("눌린 축(위 지표: ... / 아래 대상: ...)") -> role pressed + level roles; 3 pressed axes -> level roles;
    4 dual-role values -> value role.
    """
    sec = (section or "").strip()[:1]
    t = nfkc(text or "").strip()
    out = {"role": "", "level_roles": "", "value_role": "", "value_exceptions": "", "parse_note": ""}
    if not t:
        out["parse_note"] = "권장 판정이 비어 있음"
        return out
    if sec == "3":
        levels = parse_levels(t)
        if levels:
            out["level_roles"] = LEVEL_SEP.join(levels)
        else:
            out["parse_note"] = "단계 역할을 읽지 못함: 단계마다 '맨 위·위·가운데·아래 + 역할 하나'가 아님(한 단계에 역할이 섞임 등)"
        return out
    if len(_split_top(t)) > 1:
        out["parse_note"] = "권장이 '/'로 나뉨(표마다 다름 등): 역할 하나로 읽지 못함"
        return out
    if sec == "4":
        role = parse_role(t)
        if role in VALUE_OVERRIDE_ROLES:
            out["value_role"] = role
        else:
            out["parse_note"] = "값 역할 하나로 읽지 못함"
        return out
    if _head(t) == "눌린":
        out["role"] = "pressed"
        inner = _first_group(t)
        levels = parse_levels(inner) if inner else None
        if levels:
            out["level_roles"] = LEVEL_SEP.join(levels)
        else:
            out["parse_note"] = "눌린 축의 단계 역할을 읽지 못함: 괄호 안이 '위 역할 / 아래 역할' 꼴이 아님(같은 단계에 섞임 등)"
        return out
    role = parse_role(t)
    if role not in _AXIS_ROLES:
        out["parse_note"] = "축 역할 하나로 읽지 못함"
        return out
    pairs, note = parse_exceptions(t)
    if note.startswith("값 예외를 읽지 못함"):
        out["parse_note"] = note + ": 행을 적용하지 않음"
        return out
    out["role"], out["value_exceptions"], out["parse_note"] = role, format_exceptions(pairs), note
    return out


# ---------------------------------------------------------------- full table lists for cut rows

_EVID_COUNT = re.compile(r"^\s*표\s*(\d+)\s*개")
_BASIS_COUNT = re.compile(r"표\s*(\d+)\s*개")
_EVID_LIST = re.compile(r"\(표:\s*([^()]*)\)")


def split_ids(text: str) -> tuple[tuple[str, ...], bool]:
    """("A / B / C …") -> ((A, B, C), cut)."""
    text = (text or "").strip()
    cut = text.endswith(TRUNCATED_MARK)
    return tuple(t.strip() for t in text.rstrip(TRUNCATED_MARK).split("/") if t.strip()), cut


def _stated_count(row: dict) -> int | None:
    for field, pattern in (("evidence", _EVID_COUNT), ("근거", _BASIS_COUNT)):
        m = pattern.search(row.get(field) or "")
        if m:
            return int(m.group(1))
    return None


def _evidence_ids(row: dict) -> tuple[str, ...] | None:
    """Table IDs listed in the evidence groups "(표: A, B, …)"; None when a group is cut or there is none."""
    groups = _EVID_LIST.findall(row.get("evidence") or "")
    if not groups:
        return None
    ids: set[str] = set()
    for g in groups:
        g = g.strip()
        if g.endswith(TRUNCATED_MARK):
            return None
        ids |= {t.strip() for t in g.split(",") if t.strip()}
    return tuple(sorted(ids))


class DualTables:
    """Tables where a value name is a dual-role value (exact target-dictionary hit among mostly non-target siblings
    on an indicator axis), with the rules only, as the confirmation sheet's section 4 was built. Loaded on demand."""

    def __init__(self, catalog=None):
        self.catalog, self._by_key = catalog, None

    def tables(self, subject: str) -> tuple[str, ...]:
        if self._by_key is None:
            from .loader import load_catalog

            catalog = self.catalog if self.catalog is not None else load_catalog()
            roles = build_axis_roles(catalog, use_draft_csv=False, overrides=False)
            by_key: dict[str, set[str]] = {}
            for vr in roles.values.values():
                if vr.dual:
                    by_key.setdefault(base(vr.name), set()).add(vr.table_id)
            self._by_key = by_key
        return tuple(sorted(self._by_key.get(base(subject), set())))


def full_table_ids(row: dict, subject: str, dual: DualTables) -> tuple[str, str]:
    """(표 ID text, note) for one sheet row. A cut list is rebuilt when a source gives the stated number of tables
    and contains the shown IDs; else it stays cut and the note says so."""
    shown, cut = split_ids(row.get("표 ID") or "")
    if not cut:
        return " / ".join(shown), ""
    want = _stated_count(row)
    # the sheet's own list counts without a stated number (its groups are not cut); the catalog's only with one
    sources = (("확인표 근거 목록", lambda: _evidence_ids(row), False),
               ("카탈로그 겸용 값", lambda: dual.tables(subject), True))
    for label, get, needs_count in sources:
        if needs_count and want is None:
            continue
        ids = get()
        if ids and set(shown) <= set(ids) and (want is None or len(ids) == want):
            return " / ".join(ids), f"표 ID: 잘린 목록을 {label}으로 다시 만듦({len(ids)}표)"
    return (" / ".join(shown) + f" {TRUNCATED_MARK}",
            f"표 ID: 잘린 목록을 다시 만들지 못함(확인표에 적힌 표 수 {want}); 적용하지 않음")


# ---------------------------------------------------------------- conversion


MACHINE_KEYS = ("role", "level_roles", "value_role", "value_exceptions")
DECISION_FIELDS = tuple(f"decision_{k}" for k in MACHINE_KEYS)
ORPHAN_FIELDS = ("구분", "표 ID", "축 이름 또는 값", *DECISION_FIELDS, "source_row", "reason")


def _row_key(section: str, subject: str, ids_text: str) -> tuple:
    """Decision key: section, subject and the whole table-ID set (sorted)."""
    return section.strip(), subject.strip(), tuple(sorted(set(split_ids(ids_text)[0])))


def _decision_columns(section: str, row: dict) -> tuple[dict, str]:
    """decision_* columns already in a row, or read from a free-text ``decision``; (columns, note)."""
    cols = {f: (row.get(f) or "").strip() for f in DECISION_FIELDS}
    if any(cols.values()):
        return cols, ""
    text = (row.get("decision") or "").strip()
    if not text:
        return cols, ""
    read = machine_columns(section, text)
    cols = {f"decision_{k}": read[k] for k in MACHINE_KEYS}
    return cols, ("decision을 읽지 못함: " + read["parse_note"]) if read["parse_note"] else ""


def _warn(text: str) -> None:
    sys.stderr.write(f"warning: {text}\n")


def _write_orphans(path: Path, orphans: list[dict]) -> None:
    """Add orphaned decisions to the orphan file (rows already there are kept; exact repeats are not added)."""
    path = Path(path)
    rows = read_csv(path) if path.exists() else []
    seen = {tuple(r.get(f, "") for f in ORPHAN_FIELDS) for r in rows}
    for o in orphans:
        k = tuple(o.get(f, "") for f in ORPHAN_FIELDS)
        if k not in seen:
            seen.add(k)
            rows.append(o)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(ORPHAN_FIELDS), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def convert(sheet: Path, out: Path, catalog=None, orphan_path: Path | None = None) -> int:
    """Write the overrides file from the sheet; returns the number of rows.

    Decisions already in ``out`` are carried to the row with the same key (구분, subject, whole table-ID set). A
    decision whose row is gone, whose table set changed, or that the sheet's own ``decision`` replaced is not dropped
    silently: it is warned about on stderr and, when ``orphan_path`` is given, added to that file.
    """
    sheet, out = Path(sheet), Path(out)
    kept: dict[tuple, tuple[dict, dict]] = {}
    if out.exists():
        for row in read_csv(out):
            cols, _note = _decision_columns(row["구분"], row)
            if any(cols.values()):
                kept[_row_key(row["구분"], row["축 이름 또는 값"], row["표 ID"])] = (cols, row)
    dual = DualTables(catalog)
    rows, used, replaced = [], set(), []
    for n, row in enumerate(read_csv(sheet), start=2):
        subject = (row.get("축 이름") or "").strip() or (row.get("값") or "").strip()
        ids_text, ids_note = full_table_ids(row, subject, dual)
        read = machine_columns(row["구분"], row.get("recommended", ""))
        decision, decision_note = _decision_columns(row["구분"], row)
        key = _row_key(row["구분"], subject, ids_text)
        if key in kept:
            used.add(key)
            old = kept[key][0]
            if not any(decision.values()):
                decision = old
            elif decision != old:
                replaced.append(key)
        notes = "; ".join(x for x in (read["parse_note"], ids_note, decision_note) if x)
        rows.append({"구분": row["구분"], "표 ID": ids_text, "축 이름 또는 값": subject,
                     "recommended": row.get("recommended", ""), "confidence": row.get("confidence", ""),
                     "evidence": row.get("evidence", ""), **{k: read[k] for k in MACHINE_KEYS}, "parse_note": notes,
                     "status": "draft", **decision, "source_row": f"{sheet.name}:{n}"})
    new_sets: dict[tuple, list[tuple]] = {}
    for r in rows:
        k = _row_key(r["구분"], r["축 이름 또는 값"], r["표 ID"])
        new_sets.setdefault(k[:2], []).append(k[2])
    orphans = []
    for key, (cols, old_row) in kept.items():
        if key in used and key not in replaced:
            continue
        if key in replaced:
            reason = "확인표의 decision으로 바뀜"
        elif key[:2] in new_sets:
            reason = "표 집합이 바뀜: 새 표 ID " + " | ".join(" / ".join(s) for s in new_sets[key[:2]])
        else:
            reason = "새 파일에 짝이 없음"
        _warn(f"decision not carried ({reason}): {key[0]} {key[1]} [{' / '.join(key[2])}]")
        orphans.append({"구분": old_row["구분"], "표 ID": old_row["표 ID"], "축 이름 또는 값": old_row["축 이름 또는 값"],
                        **cols, "source_row": old_row.get("source_row", ""), "reason": reason})
    if orphans and orphan_path is not None:
        _write_orphans(orphan_path, orphans)
        _warn(f"{len(orphans)} decisions kept in {Path(orphan_path).name}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(OVERRIDE_FIELDS))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    sheet = Path(argv[0]) if argv else paths.CONFIRM_SHEET_RECOMMENDED
    out = Path(argv[1]) if len(argv) > 1 else paths.AXIS_ROLE_OVERRIDES
    orphan = Path(argv[2]) if len(argv) > 2 else paths.ORPHAN_DECISIONS
    n = convert(sheet, out, orphan_path=orphan)
    print(f"wrote {n} override rows")
    return 0


if __name__ == "__main__":
    from .. import _guard

    _guard.install()
    raise SystemExit(main())
