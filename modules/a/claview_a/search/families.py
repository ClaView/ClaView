"""Cell cover of table selection (plan 04, 4-3 to 4-5): 3.5 cover_cells and the 3.7 recheck ``verify_choice``.

Requested cells of a requirement = target values x period segments x frequency x basis values. A dimension the
question does not set is not split: no target -> one cell; no frequency -> frequencies are not split (B picks one);
no period -> "the latest" (decision 2); a basis axis with one value is a condition every cell needs, an axis with two
or more values of the requirement is split.

Cells a table provides: for every (frequency, that frequency's period range) pair of the table, its targets x the
period segments inside the range x the basis values it does not contradict. Frequency and period are never
multiplied apart: a table with yearly data from 2000 and monthly data from 2010 gives no monthly cell before 2010.
Period segments are the resolved requested period cut at the period edges of the candidate tables; the requested
period is never cut back to the data (a relative period running past every candidate within the snapshot lag is
only marked ``period_lag``; its open part stays uncovered). "The latest" (no period) is computed among the tables
that meet the requirement's conditions (no basis or exclusion conflict, a requested target present): within one
family only the tables reaching that family's latest end give it, and only when that end is within the snapshot lag
of the latest end of all such candidates.

Search (4-3): per family the least table group covering every requested cell is searched exactly after dominated
tables are removed (cells a subset of another candidate's and a rank score not higher). More than 12 tables left ->
the 12 best by rank score are searched exactly, the rest is added greedily (most uncovered cells, then rank) for
cells the 12 leave open; marked ``search_truncated``; never searched exponentially. Same size -> higher rank-score
sum. No family covers everything alone -> the same search over every candidate (families mixed). Order of
alternatives (4-3): complete first, more covered cells, fewer tables, rank-score sum, evidence tier.

Unspecified dimensions (4-4, decision 5): tables of one family differ by a dimension when their basis-axis values
(read from values and title: 원계열/계절조정, 명목/실질, 말잔/평잔 ...) or their non-basis bracket words (source,
classification system, agency; year ranges left out) differ. A family member outside the chosen group that gives
any requested cell a chosen table gives, stands on every basis word of the requirement at least as well, and differs
from it by such a dimension, is put in; so are chosen tables that differ among themselves. All such tables of the
requirement form one mark (its dimension names every axis involved) with one cap of 3 tables, most recent start
first; past the cap ``more`` is marked, and a chosen table the cover still needs is kept.

3.7 ``verify_choice`` re-runs the checks on the chosen tables and returns, besides the cover, every own or shared
condition of the requirement a chosen table does not pass (``unproven``): decide_state allows auto only without
them (state.py).

Nothing here names a table, case or question.
"""

from __future__ import annotations
import json
import re
from .. import paths
from ..catalog.relations import relations_of
from ..conditions.words import BASIS_AXES
from .words import HANDOFF_ONLY_AXES
from .tables import nospace


_BRACKET = re.compile(r"\(([^()]*)\)")


_YEAR_RANGE = re.compile(r"~?\d{4}(?:\.\d{1,2})?(?:~(?:\d{2,4}(?:\.\d{1,2})?)?)?|\d{4}-\d{4}")
SIGNATURE_AXES = [a for a in BASIS_AXES if a not in HANDOFF_ONLY_AXES]
_BASIS_FORMS = {nospace(f) for a in SIGNATURE_AXES for fs in BASIS_AXES[a].values() for f in fs}
POLICY = json.loads(paths.SELECTION_POLICY.read_text(encoding="utf-8"))
AXIS_KINDS: dict[str, str] = POLICY["basis_axis_kinds"]
FREQUENCY_WORDS = frozenset(nospace(w) for w in POLICY["frequency_words"])
SOURCE_WORDS = frozenset(nospace(w) for w in POLICY["source_words"])
AGENCY_WORDS = tuple(POLICY["agency_axis_words"])


def signature(view) -> dict[str, frozenset]:
    """Basis-axis values a table carries, read from its values and its title (axis -> values)."""
    from .basis import values

    texts = [view.title] + [getattr(it, "name_raw", "") or "" for it in getattr(view, "items", [])]
    out = {}
    for axis in SIGNATURE_AXES:
        got = frozenset(v for text in texts for v in values(text, axis))
        if got:
            out[axis] = got
    return out


def bracket_words(name: str, merge_basis: bool = False) -> dict[str, str]:
    """Bracket words of a table name, cut at commas, middle dots and spaces, with their class: basis (holds a basis
    word: read by ``signature``), frequency, period (a year range or a year), source, other. Pieces of digits and
    marks only ("2010=100") are kept (other unless they are a year range). ``merge_basis`` (plan 36b, the approved
    ask path only): a piece whose words joined hold a basis form ("잔액 기준" for 잔액기준) is one basis word, so its
    split words are no leftover of another kind."""
    out: dict[str, str] = {}
    for m in _BRACKET.finditer(name or ""):
        for piece in re.split(r"[,·]+", m.group(1)):
            if nospace(piece) in FREQUENCY_WORDS:  # a frequency phrase ("분기 및 연간") is one word
                out[nospace(piece)] = "frequency"
                continue
            if merge_basis and len(piece.split()) > 1 and any(f in nospace(piece) for f in _BASIS_FORMS):
                out[nospace(piece)] = "basis"
                continue
            for part in piece.split():
                _classify(nospace(part), out)
    return out


def _classify(word: str, out: dict[str, str]) -> None:
    if word:
        if any(f in word for f in _BASIS_FORMS):
            kind = "basis"
        elif word in FREQUENCY_WORDS:
            kind = "frequency"
        elif _YEAR_RANGE.fullmatch(word):
            kind = "period"
        elif word in SOURCE_WORDS:
            kind = "source"
        else:
            kind = "other"
        out[word] = kind


def agency_values(view) -> frozenset:
    """Values of every axis whose name holds an agency or source word (출처, 기관, 작성 ...), whatever the axis
    name: the two tables are compared on the union."""
    objs = {obj for obj, name in getattr(view, "dims", []) if any(w in (name or "") for w in AGENCY_WORDS)}
    return frozenset(it.name_raw for it in getattr(view, "items", []) if getattr(it, "obj_id", None) in objs)


def _agency_diff(a, b) -> bool:
    return agency_values(a) != agency_values(b)


def _axis_values(view, indicator: bool) -> frozenset:
    """(axis name, value) pairs of the table: the non-indicator axes (``indicator`` False) or every axis."""
    roles = getattr(view, "axis_roles", {}) or {}
    names = dict(getattr(view, "dims", []))
    return frozenset((names.get(it.obj_id, it.obj_id), it.name_raw) for it in getattr(view, "items", [])
                     if (indicator or roles.get(it.obj_id) != "indicator")
                     and not any(f in nospace(it.name_raw) for f in _BASIS_FORMS))  # basis values: signature


def _frequencies(view) -> frozenset:
    return frozenset(f for f, _, _ in view.periods)


def dimension_kinds(a, b, merge_basis: bool = False) -> dict[str, list[str]]:
    """Kinds of the dimensions two family tables differ by (decision 5 as changed, research note section 2): kind
    -> what differs. A safe kind (period, source, frequency, survey) is given only when it is shown; whatever is not
    compared or not classified is ``other`` (confirm):
    - basis-axis values (``signature``): mixed_basis / survey / other by ``basis_axis_kinds``;
    - bracket words not on a basis axis, word by word: frequency, period (year ranges), source, else other;
    - agency/source axis values (whatever the axis names) and the values of the non-indicator axes: other;
    - frequency when only the frequencies differ;
    - source when the names, axes and all values are the same (only periods or the source differ);
    - anything else unexplained: other.
    ``merge_basis``: read the bracket words with ``bracket_words(merge_basis=True)`` (plan 36b, approved ask only)."""
    out: dict[str, list[str]] = {}
    sa, sb = signature(a), signature(b)
    for axis in sorted(set(sa) | set(sb)):
        if sa.get(axis, frozenset()) != sb.get(axis, frozenset()):
            out.setdefault(AXIS_KINDS.get(axis, "other"), []).append(axis)
    wa, wb = bracket_words(a.title, merge_basis), bracket_words(b.title, merge_basis)
    for word in sorted(set(wa) ^ set(wb)):
        kind = (wa.get(word) or wb.get(word))
        if kind != "basis":
            out.setdefault(kind, []).append(word)
    if _agency_diff(a, b):
        out.setdefault("other", []).append("agency_values")
    if _axis_values(a, False) != _axis_values(b, False):
        out.setdefault("other", []).append("axis_values")
    if _frequencies(a) != _frequencies(b):
        out.setdefault("frequency", []).append("frequencies")
    if not out:
        same = a.title == b.title and _axis_values(a, True) == _axis_values(b, True)
        out["source" if same else "other"] = [] if same else ["unexplained"]
    return out


def family_members(layer, family: str) -> list[str]:
    if family.startswith("table:"):
        return [family.split(":", 1)[1]]
    return list(relations_of(layer).family_tables.get(family, []))
