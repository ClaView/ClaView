"""Scoring of the table-level path (plan 37, step 4; users 2026-10-28 03:55 and 04:20 UTC).

``adapt`` links one run (the graph state) into the model's own output, code's checks, code's corrections and the
final result, with code's word-level diagnostics kept apart and the run-time information (``run_info``) kept as the
run recorded it. ``score_case`` scores one question against the standard; ``aggregate`` counts by part.

Rules (the 04:20 UTC review):
- the model's output is checked by the scorer itself first (references, quotes, evidence by its own choice row and
  table, ask option references); the result is then compared with what code did: an error code caught is
  ``code_check_success``, an error code let through is ``code_check_missed``, a fact error code reported that the
  scorer does not find is ``code_false_reject``; too little to tell is ``review``. A period code stop is
  ``unsupported_expression`` only for a quote of the fixed unsupported forms, ``code_false_reject`` for a quote of
  the fixed supported forms, else ``review`` (no period reader here);
- statistics are mapped to the standard's groups by table only (a representative, an ask option or a final table in
  the group or a confirmed equivalent); a statistic in no group, in several, or a group with several statistics is
  never guessed; tables, months, frequency and basis are checked per mapped statistic, months joined only inside one
  statistic, with gaps, overlaps and tables outside the group found;
- an ask is scored on its statistic, axes, values, value tables and the other statistics' outcomes together; a
  statistic the model only asks about has its meaning scored by that ask against the standard's ask (axis, every
  value once, each value's table allowed for that value), never by putting every option table in the group;
- notation allowed by the shared contract, read here from the raw output and the data file, not from code's verdict
  (user 2026-10-28 05:01 UTC): a quoted card text may differ by spaces within one table and kind (two card texts
  equal but for spaces: ``evidence_normalization_ambiguous``); an ask option value is mapped only by the axis's
  explicit forms in ``ask_axes.json`` value_forms;
- an ask's options are checked by the scorer itself (resolution, one card, distinct tables, written value mapped by
  the shared forms and carried by the option's table: ``ask_facts``) in the fact check as in the ask score; a code
  rejection the scorer cannot decide (an option table whose axis value it cannot read) is ``code_check_review``
  (listed under review), never
  ``code_false_reject`` (user 2026-10-28 05:28 UTC);
- results kept apart (users 2026-10-28 05:37 and 05:42 UTC; definitions in ``METRICS``): model meaning, model
  evidence, code handling, final selection (``final_full``, its definition unchanged: tables, months, frequency,
  basis, coverage or the expected ask, whatever the evidence), and completion (``completed``: ``final_full`` right
  and every statistic's card evidence verified, i.e. a verified handoff); ``contract`` stays the whole-output fact
  check;
- confirmed, proposed and unscored parts stay apart: a pool that holds only a proposed equivalent is ``review``; a
  question whose conditions are unconfirmed gets ``table_match`` only, never ``final_full``;
- each metric reports right, wrong, review, not applicable and the denominator (right + wrong).
The scorer never calls the path's selection or check functions, and never reads a diagnosis.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

# .21: a card's listed axis values are item facts of its tables, as in the path (cards .11, user 10:40 UTC)
# .22: a standard may mark each expectation item (representative, ask, period_frequency) confirmed, insufficient or
# policy_hold (``expectation_items``, plan 52, user 2026-10-29 16:08 UTC); an unconfirmed item is held, never right
# or wrong, and confirmed fact, contract and evidence errors still fail. Standards without it score as before.
# .23: ask options of several cards are no problem for a record of the policy that allows them (check .29 and later,
# plan 56 order 8, ⑨); older records are scored by their own rule
SCORE_VERSION = "tl-score-2026-11-02.24"
SEVERAL_CARDS_FROM = ("2026-10-31", 29)  # the first check version that allows ask options of several cards


def several_cards_allowed(rec: dict) -> bool:
    """Whether the record's check version allows ask options of several cards (``SEVERAL_CARDS_FROM``)."""
    v = ((rec.get("run_info") or {}).get("versions") or {}).get("check") or ""
    m = re.fullmatch(r"tl-check-(\d{4}-\d{2}-\d{2})\.(\d+)", v)
    return bool(m) and (m.group(1), int(m.group(2))) >= SEVERAL_CARDS_FROM
ITEM_HELD_STATES = ("insufficient", "policy_hold")
# answer contracts whose ask options carry evidence (a list, plan 56 order 6-3; the scorer reads no code of the path)
OPTION_EVIDENCE_CONTRACTS = ("tl-contract-2026-10-28.3", "tl-contract-2026-10-28.4", "tl-contract-2026-10-28.5",
                             "tl-contract-2026-10-28.6", "tl-contract-2026-10-28.7", "tl-contract-2026-10-28.8",
                             "tl-contract-2026-10-28.9")
VALUE_FORMS = json.loads((Path(__file__).resolve().parents[2] / "claview_a" / "data" /
                          "ask_axes.json").read_text(encoding="utf-8"))["value_forms"]["forms"]
CITATION_FACT_CODES = ("evidence_not_on_card", "evidence_of_other_table", "evidence_normalization_ambiguous",
                       "evidence_kind_mismatch")
METRICS = {  # tl-score-2026-10-28.9 (05:58 UTC review: code_checks_right and completed stricter; 06:08 UTC: asks
    # matched per statistic and axis; 06:27 UTC: a standard basis value read with the shared explicit forms)
    "contract_ok": "every fact of the model output holds by the scorer's own check (quotes, references, evidence, "
                   "ask options); unchanged",
    "model_meaning_right": "the model's representatives, basis and asks match the standard; unchanged",
    "evidence_contract_ok": "model evidence: every card evidence of every choice is a fact of the chosen table's card "
                            "of the stated kind, by the scorer's own check (new in .6)",
    "code_checks_right": "code handling of fact errors only (detecting and recording them; not a verdict on code's "
                         "basis, ask or period decisions), error by error (statistic, kind, target): every fact error the scorer "
                         "finds is caught by code (else code_check_missed) and code reports none the scorer finds "
                         "absent (code_false_reject when it stopped the statistic, code_false_report when only "
                         "recorded); review when the scorer cannot decide (new in .6, matched per error in .7)",
    "table_match": "the final tables (or the expected ask) match the standard; unchanged",
    "final_full": "final selection: tables, months, frequency, basis and coverage (or the expected ask) right, "
                  "whatever the evidence; definition unchanged, an unverified outcome is scored on its tables",
    "held_statistics": "per question and summed: statistics code held (basis_axis_held, target_table_unconfirmed); "
                       "fixed before the run (user 2026-10-28 07:03 UTC)",
    "ask_missing": "per (statistic, axis), counted per question and summed: an ask the standard needs that the model "
                   "did not make (the standard's ask names no statistic: the unit is the question and the axis)",
    "ask_unneeded": "per (statistic, axis): a model ask on an axis the standard does not need",
    "option_meaning_errors": "per option value of a model ask on the needed axis: a value with a table not allowed "
                             "for it, a value the standard does not have, or a needed value left out",
    "option_evidence_errors": "per option value: option evidence that is not a fact of the option table's card; "
                              "not measurable for answers to a contract without option evidence (counted apart)",
    "model_table_set": "the set of the model's representatives (choices) against the answer groups, whatever the "
                       "statistic split: right when every group holds one and none lies outside them; missing groups "
                       "(a statistic left out) and extra tables (one added) are listed; na when an ask is expected "
                       "(new in .12, user 2026-10-28 07:56 UTC: several statistics may use one table)",
    "final_table_set": "the same on the final tables, with the confirmed frequency per table and the confirmed "
                       "period per statistic (its tables' months joined: missing, extra or repeated months fail; "
                       "``periods`` checked like ``period``, .14) (new in .12); review when the final conditions are "
                       "unconfirmed",
    "requirement_targets": "only for a question whose standard confirms its requirement targets (each with its "
                           "question text, answer series and allowed tables; user 2026-10-28 08:13 UTC): each target "
                           "is covered by the model statistics whose quote holds the target's text, merged or split; "
                           "a target is right when every covering statistic's representative (model) or final tables "
                           "and conditions (final) are allowed for it; no covering statistic is missing (review when "
                           "a statistic quote cannot be placed in the question); a statistic covering no target is "
                           "review; the number of output rows is never forced, and a series left out inside an "
                           "allowed table is not detectable (new in .13). model_meaning and final_full use these "
                           "targets in place of the table groups for such a question. .19: a target with qualifiers "
                           "is covered only by a statistic whose quote or own splitter quote holds them (review "
                           "otherwise, never placed by its table)",
    "completed": "complete: final_full right, no fact error found by the scorer's own check, no statistic left "
                 "with its card evidence unverified, and the run routed handoff or ask; review when final_full is "
                 "right but code handling is undecidable (new in .6, checked against the scorer in .7)",
}
MODEL_FACT_CODES = ("quote_not_in_question", "evidence_not_on_card", "evidence_of_other_table",
                    "evidence_normalization_ambiguous",
                    "evidence_kind_mismatch", "table_not_in_pool", "ask_invalid")
PERIOD_STOP_CODES = ("period_unresolved", "period_partially_read", "frequency_unresolved")
PARTS = ("golden16", "new24", "all")
# Fixed support standard of period quotes (forms the step-3 tests verify the path reads, and forms it is known not to
# read). A quote of neither list is ``review``.
SUPPORTED_PERIOD_FORMS = [re.compile(p) for p in (
    r"\d{4}년\s*\d{1,2}\s*~\s*\d{1,2}월", r"\d{4}년\s*\d{1,2}월\s*~\s*(\d{4}년\s*)?\d{1,2}월",
    r"\d{4}년\s*\d{1,2}월\s*부터\s*(\d{4}년\s*)?\d{1,2}월\s*까지", r"\d{4}\s*~\s*\d{4}년", r"\d{4}년\s*부터\s*\d{4}년\s*까지",
    r"\d{4}년\s*[1-4]\s*~\s*[1-4]\s*분기", r"\d{4}년\s*[1-4]분기\s*부터\s*\d{4}년\s*[1-4]분기\s*까지",
    r"\d{4}년(\s*이후|\s*부터)?", r"최근\s*\d{1,2}\s*년간?", r"\d{4}년\s*(과|와|,)\s*\d{4}년", r"\d{4}년\s*[상하]반기")]
UNSUPPORTED_PERIOD_FORMS = [re.compile(p) for p in (
    r"(한|두|세|석|네|넉)\s*(개월|달|분기|해)", r"이후\s*\d+\s*개월", r"팬데믹", r"\d{4}\s*,\s*\d{4}년")]
# .17 (user 2026-10-28 20:24 UTC): the scorer's own fixed reading of whether a question states a frequency or a
# period, for the condition diagnosis only (never for the path's selection). An end word is a frequency with an end
# point; a point in time ("7월말", "2024년 말", "지난 월말") and a basis word ("월말잔액", "월말 잔액") are not.
FREQUENCY_FORMS = re.compile(r"월별|매월|매달|월간|달마다|월마다|분기별|분기마다|매\s*분기|반기별|반기마다|연간|연도별|"
                             r"해마다|매년|년도별|일별|매일|주별|매주")
END_FORMS = re.compile(r"(?<![0-9가-힣])(월말|분기말|반기말|연말|년말)(?!\s*잔)")
END_POINT_BEFORE = re.compile(r"(지난|이번|저번|전|올|작|금)\s*$")
PERIOD_FORMS = re.compile(r"(?<!\d)\d{4}(?![\d=]|\s*=)|최근|올해|작년|지난해|재작년|금년|이후|부터|까지|"
                          r"그동안|지금까지")  # a base-year mark (2020=100) is no period
CONDITION_STATES = ("not_stated", "quoted", "other_field", "missing", "not_kept", "review")


# ---------------------------------------------------------------- adapter


def adapt(state: dict) -> dict:
    """The run record in linked parts (+ diagnostics and the run-time information) for scoring."""
    record = state.get("record") or {}
    cards = state.get("cards") or []
    out = {"question": state.get("question"), "as_of": state.get("as_of"), "run_info": state.get("run_info"),
           "error": record.get("error"), "pool": [r["table_id"] for c in cards for r in c["tables"]],
           "cards": [{"card_id": c["card_id"], "name": c["name"],
                      "tables": [{"table_id": r["table_id"], "title": r["title"], "brackets": list(r["brackets"]),
                                  "bracket_words": list(r["bracket_words"])} for r in c["tables"]],
                      "matched": copy.deepcopy(c["matched"]),
                      "axis_items": copy.deepcopy(c.get("axis_items") or [])} for c in cards],
           "model": None, "checks": {}, "corrections": {}, "final": {}, "diagnostics": {},
           "route": (record.get("route") or {}).get("action"),
           "answer_contract": (record.get("parse") or {}).get("answer_contract")}
    if record.get("model_output") is None:
        return out
    out["model"] = copy.deepcopy(record["model_output"])  # the model's own output, refs as written
    for d in record["decisions"]:
        sid = d["statistic"]
        out["checks"][sid] = {"reasons": d["reasons"], "warnings": d["warnings"],
                              "citation": (d.get("citation") or {}).get("problems", [])}
        out["corrections"][sid] = d["corrections"]
        out["final"][sid] = {"outcome": d["outcome"], "tables": d["final_tables"], "asks": d["asks"],
                             "representative": d["representative"],
                             "frequency_quotes": copy.deepcopy(d.get("frequency_quotes") or [])}  # .14: recorded only
        out["diagnostics"][sid] = d.get("diagnostics", [])
    return out


# ---------------------------------------------------------------- the scorer's own facts


def _ns(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _resolve(rec: dict, ref: str) -> str | None:
    m = re.fullmatch(r"(g\d+)\.t(\d+)", ref or "")
    if m:
        card = next((c for c in rec["cards"] if c["card_id"] == m.group(1)), None)
        i = int(m.group(2))
        return card["tables"][i - 1]["table_id"] if card and 1 <= i <= len(card["tables"]) else None
    return ref if ref in rec["pool"] else None


def _card_texts(rec: dict, table: str) -> dict[str, set[str]]:
    for c in rec["cards"]:
        for r in c["tables"]:
            if r["table_id"] == table:
                parts = {p.strip() for b in r["brackets"] for p in re.split(r"[,·]", b) if p.strip()}
                return {"title": {r["title"], c["name"]},
                        "bracket": set(r["brackets"]) | set(r["bracket_words"]) | parts,
                        "item": {t for it in [*(i for m in c["matched"] for i in m["items"]),
                                              *(c.get("axis_items") or [])] if table in it["tables"]
                                 for t in (it["name"], _shown(it))}}
    return {}


def _shown(it: dict) -> str:
    """A value as a card shows it (name, then the catalog parent and unit), read from the record."""
    extra = [x for x in ((f"상위 {it['parent']}" if it.get("parent") else None),
                         (f"단위 {it['unit']}" if it.get("unit") else None)) if x]
    return it["name"] + (f" ‹{' / '.join(extra)}›" if extra else "")


def _fact_kinds(rec: dict, table: str, text: str) -> dict[str, int]:
    """Kind -> how many card texts of ``table`` equal ``text`` (exactly: 1; else spaces aside: the count)."""
    out = {}
    for kind, texts in _card_texts(rec, table).items():
        if text in texts:
            out[kind] = 1
        elif _ns(text) and (n := sum(_ns(t) == _ns(text) for t in texts)):
            out[kind] = n
    return out


def ask_facts(rec: dict, titles: dict | None = None) -> tuple[dict[tuple, set[str]], dict[tuple, list[str]]]:
    """The scorer's own check of the model's asks, kept per statistic and axis (review 2026-10-28 06:08 UTC: one
    axis's error or undecided option never spreads to another axis of the same statistic): (statistic, axis) ->
    problems found (named as code names them), and (statistic, axis) -> what it could not decide (no titles, or an
    option table whose axis value it cannot read)."""
    found: dict[tuple, set[str]] = {}
    undecided: dict[tuple, list[str]] = {}
    card_of = {r["table_id"]: c["card_id"] for c in rec["cards"] for r in c["tables"]}
    for a in rec["model"]["asks"]:
        key = (a["for"], a["axis"])
        probs, und = found.setdefault(key, set()), undecided.setdefault(key, [])
        tables = [_resolve(rec, o["table"]) for o in a["options"]]
        if any(t is None for t in tables):
            probs.add("option_not_in_pool")
            continue
        if len({card_of.get(t) for t in tables}) != 1 and not several_cards_allowed(rec):
            probs.add("options_in_several_cards")
        if len(set(tables)) != len(tables):
            probs.add("same_table_twice")
        for o, t in zip(a["options"], tables):
            named = _written_value(a["axis"], o["value"])
            if named is None:
                probs.add("option_value_ambiguous")
            elif titles is None:
                und.append(f"option {o['value']!r}: no titles given")
            elif not (have := _table_value(titles, t, a["axis"])):
                und.append(f"option {o['value']!r}: no {a['axis']} value of {t} readable from its title")
            elif named not in have:
                probs.add("option_value_not_on_table")
    return found, undecided


def _group(code: str) -> str:
    return "evidence" if code.startswith("evidence_") else code


def _code_key(code: str, detail) -> str | None:
    """The target of a fact error as code records it (quote, evidence text, table reference, ask axis)."""
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        for k in ("text", "axis", "table", "quote"):
            if detail.get(k) is not None:
                return str(detail[k])
    return None


def fact_items(rec: dict, titles: dict | None = None) -> tuple[set[tuple], set[tuple]]:
    """The scorer's own fact errors one by one, as (statistic, group, target), and the ones it cannot decide."""
    m, q = rec["model"], rec["question"]
    found, undecided = set(), set()
    for s in m["statistics"]:
        if _ns(s["quote"]) not in _ns(q):
            found.add((s["id"], "quote_not_in_question", s["quote"]))
    for key in ("splitters", "periods", "frequencies"):
        for row in m[key]:
            if _ns(row["quote"]) not in _ns(q):
                for sid in row["for"]:
                    found.add((sid, "quote_not_in_question", row["quote"]))
    for c in m["choices"]:
        table = _resolve(rec, c["table"])
        for sid in c["for"]:
            if table is None:
                found.add((sid, "table_not_in_pool", c["table"]))
                continue
            for ev in c["evidence"]:
                kinds = _fact_kinds(rec, table, ev["text"])
                if kinds.get(ev["kind"]) != 1:
                    found.add((sid, "evidence", ev["text"]))
    for a in m["asks"]:  # contract .3: the model's evidence per option table
        for o in a["options"]:
            table = _resolve(rec, o["table"])
            for ev in o.get("evidence", []):
                if table is not None and _fact_kinds(rec, table, ev["text"]).get(ev["kind"]) != 1:
                    found.add((a["for"], "evidence", ev["text"]))
    probs, und = ask_facts(rec, titles)
    for a in m["asks"]:
        if probs.get((a["for"], a["axis"])):
            found.add((a["for"], "ask_invalid", a["axis"]))
        elif und.get((a["for"], a["axis"])):
            undecided.add((a["for"], "ask_invalid", a["axis"]))
    return found, undecided


def _stated(question: str) -> dict:
    """Frequency and period expressions the question states, by the scorer's fixed forms."""
    freq = [m.group(0) for m in FREQUENCY_FORMS.finditer(question)]
    ends = [m.group(0) for m in END_FORMS.finditer(question) if not END_POINT_BEFORE.search(question[:m.start()])]
    return {"frequency": freq + ends, "end": ends, "period": [m.group(0) for m in PERIOD_FORMS.finditer(question)]}


OWN_FIELD = {"frequency": "frequencies", "period": "periods"}
QUOTE_FIELDS = ("frequencies", "periods", "splitters", "calculations")
PROBLEM_STATES = ("missing", "other_field", "not_kept", "review")  # summary precedence, worst first
DIAGNOSIS_SCOPE = ("the scorer's fixed forms only (FREQUENCY_FORMS, END_FORMS, PERIOD_FORMS): a frequency or period "
                   "written in other words is not detected and reads as not stated; a statistic is named for a "
                   "condition only through the model's own for, never by position in the question")


def _spans(text: str, sub: str) -> list[tuple[int, int]]:
    out, i = [], text.find(sub) if sub else -1
    while i >= 0:
        out.append((i, i + len(sub)))
        i = text.find(sub, i + 1)
    return out


def _occurrences(question: str) -> list[dict]:
    """Each frequency, end and period expression the question states by the fixed forms, with its place."""
    occ = [{"kind": kind, "text": m.group(0), "span": (m.start(), m.end()), "end": False}
           for kind, rx in (("frequency", FREQUENCY_FORMS), ("period", PERIOD_FORMS)) for m in rx.finditer(question)]
    occ += [{"kind": "frequency", "text": m.group(0), "span": (m.start(), m.end()), "end": True}
            for m in END_FORMS.finditer(question) if not END_POINT_BEFORE.search(question[:m.start()])]
    return sorted(occ, key=lambda o: o["span"])


def _quote_rows(m: dict) -> list[dict]:
    rows = [{"field": f, "quote": r["quote"], "for": list(r["for"])} for f in QUOTE_FIELDS for r in m.get(f, [])]
    return rows + [{"field": "statistics", "quote": st["quote"], "for": [st["id"]]} for st in m.get("statistics", [])]


def _cover(question: str, occ: dict, rows: list[dict]) -> tuple[list[dict], bool]:
    """(rows whose quote spans the expression, ambiguous): ambiguous when a quote's text appears more often in the
    question than rows quote it, so which place each row means cannot be told."""
    hits = [r for r in rows
            if any(a <= occ["span"][0] and occ["span"][1] <= b for a, b in _spans(question, r["quote"]))]
    ambiguous = any(len(_spans(question, r["quote"])) > sum(1 for x in rows if x["quote"] == r["quote"]) for r in hits)
    return hits, ambiguous


def _not_run(rec: dict, sid: str) -> list[str] | None:
    """Why code did not reach the periods step for ``sid`` (its stop codes, or its outcome), else None."""
    fin = (rec.get("final") or {}).get(sid) or {}
    if fin.get("outcome") in (None, "not_in_pool"):
        return [fin.get("outcome") or "no_decision"]
    reasons = [r["code"] for r in ((rec.get("checks") or {}).get(sid) or {}).get("reasons", [])]
    return reasons or None


def condition_details(rec: dict) -> dict:
    """Condition diagnosis by statistic, condition and quote (.18, user 2026-10-29 04:59 UTC):
    ``stated``: one row per expression the question states (fixed forms), with the field that quotes it and the
    statistics of that quote's ``for`` (None when nothing quotes it and the question has more than one statistic:
    never placed by position); state ``quoted`` / ``other_field`` / ``missing`` / ``review`` (a repeated quote
    text whose place cannot be told).
    ``delivered``: one row per (own-field quote, statistic in its ``for``): ``quoted`` when the statistic's final
    record holds it (an end word with its end point), ``not_kept`` when the record lost the end point,
    ``review`` when the word was not read, ``not_run`` (with the stop codes) when code stopped the statistic before
    the periods step, never counted as a preservation failure. Records made before ``frequency_quotes`` existed
    get no delivery rows. Another statistic's end point never stands in.
    ``summary``: the six states per kind, worst first (``not_stated`` when the question states none)."""
    m, q = rec["model"], rec["question"] or ""
    rows, sids = _quote_rows(m), [st["id"] for st in m.get("statistics", [])]
    stated = []
    for occ in _occurrences(q):
        own = [r for r in rows if r["field"] == OWN_FIELD[occ["kind"]]]
        hits, ambiguous = _cover(q, occ, own)
        field = OWN_FIELD[occ["kind"]] if hits else None
        state = ("review" if ambiguous else "quoted") if hits else None
        if not hits:
            hits, ambiguous = _cover(q, occ, [r for r in rows if r["field"] != OWN_FIELD[occ["kind"]]])
            field = hits[0]["field"] if hits else None
            state = "other_field" if hits else "missing"
        fors = sorted({x for r in hits for x in r["for"]}) if hits and not ambiguous else \
            (sids if len(sids) == 1 else None)
        stated.append({"kind": occ["kind"], "text": occ["text"], "span": list(occ["span"]), "end": occ["end"],
                       "field": field, "statistics": fors, "state": state})
    delivered = []
    finals = rec.get("final") or {}
    recorded = any("frequency_quotes" in (f or {}) for f in finals.values())
    for r in m.get("frequencies", []) if recorded else []:
        end_word = bool(END_FORMS.search(r["quote"]))
        for sid in r["for"]:
            got = [x for x in (finals.get(sid) or {}).get("frequency_quotes") or [] if x.get("quote") == r["quote"]]
            row = {"kind": "frequency", "quote": r["quote"], "statistic": sid, "end": end_word}
            stop = _not_run(rec, sid) if not got else None
            if stop:
                row.update(state="not_run", stop=stop)
            elif not got:
                row["state"] = "not_kept"
            elif any(x.get("frequency") is None for x in got):
                row.update(state="review", problem=got[0].get("problem"))
            elif end_word and not any(x.get("point_in_period") == "end" for x in got):
                row["state"] = "not_kept"
            else:
                row["state"] = "quoted"
            delivered.append(row)
    summary = {}
    for kind in ("frequency", "period"):
        here = [x["state"] for x in stated if x["kind"] == kind] + \
            [x["state"] for x in delivered if x["kind"] == kind and x["state"] != "not_run"]
        if not any(x["kind"] == kind for x in stated):
            summary[kind] = "not_stated"
        else:
            summary[kind] = next((st for st in PROBLEM_STATES if st in here), "quoted")
    return {"stated": stated, "delivered": delivered, "summary": summary, "scope": DIAGNOSIS_SCOPE}


def condition_quotes(rec: dict) -> dict:
    """The six states per kind (``condition_details`` summary): ``not_stated``, ``quoted``, ``other_field``,
    ``missing``, ``not_kept``, ``review``. Never decides a meaning."""
    return condition_details(rec)["summary"]


def condition_problems(details: dict) -> list[dict]:
    """The rows of ``condition_details`` with a problem, each with its statistics (None: not attributable)."""
    out = [{"kind": x["kind"], "text": x["text"], "state": x["state"], "statistics": x["statistics"],
            "field": x["field"]} for x in details["stated"] if x["state"] in PROBLEM_STATES]
    return out + [{"kind": x["kind"], "text": x["quote"], "state": x["state"], "statistics": [x["statistic"]],
                   "field": OWN_FIELD[x["kind"]]} for x in details["delivered"] if x["state"] in PROBLEM_STATES]


def condition_counts(details: dict) -> dict:
    """Problem counts kept apart: problem rows by state, statistics with a problem, rows no statistic is named for."""
    probs = condition_problems(details)
    return {"by_state": {st: sum(1 for x in probs if x["state"] == st) for st in PROBLEM_STATES},
            "statistics": sorted({sid for x in probs for sid in x["statistics"] or []}),
            "unattributed": sum(1 for x in probs if x["statistics"] is None),
            "not_run": sum(1 for x in details["delivered"] if x["state"] == "not_run")}


def item_held(case: dict, item: str) -> str | None:
    """The state of one expectation item the standard marks unconfirmed (``insufficient`` or ``policy_hold`` in
    ``expectation_items``, .22), else None: such an item is held. A standard without it holds nothing here (the
    golden standards' own ``item_status`` records sources and is not read)."""
    state = ((case.get("expectation_items") or {}).get(item) or {}).get("state")
    return state if state in ITEM_HELD_STATES else None


def ask_policy_held(case: dict) -> bool:
    """Whether an ask is due is not decided for this question: a held or proposed policy, or a standard that says so
    in its ask (``policy_undecided``, user 2026-10-28 08:13 UTC: CV-AB-10), or an ask item marked unconfirmed
    (.22). Only the ask part is held."""
    return case["new_policy"]["status"] in ("proposed", "hold") or case["ask"]["expected"] == "policy_undecided" \
        or item_held(case, "ask") is not None


def table_sets(rec: dict, case: dict) -> dict:
    """Set-level table scores (``METRICS``): statistics may be split per table or per item, so the model's tables
    and the final tables are compared with the answer groups as sets. Not a statistic mapping: a statistic's own
    meaning is never judged here."""
    groups = case["representative"]["confirmed_groups"]
    # .22: an expected ask whose item is unconfirmed holds only the ask; the tables are still scored as sets
    if (case["ask"]["expected"] == "required" and item_held(case, "ask") is None) or not groups:
        return {"model": {"right": "na"}, "final": {"right": "na"}}
    m = rec.get("model") or {"choices": []}

    def judge(tables: list[str], status_of) -> dict:
        tables = [t for t in dict.fromkeys(tables) if t]
        covered = [gi for gi in range(len(groups)) if any(status_of(case, t, gi) in ("right", "review")
                                                         for t in tables)]
        extra = [t for t in tables if not any(status_of(case, t, gi) in ("right", "review")
                                              for gi in range(len(groups)))]
        missing = [groups[gi] for gi in range(len(groups)) if gi not in covered]
        review = any(status_of(case, t, gi) == "review" for t in tables for gi in range(len(groups)))
        right = False if missing or extra else None if review else True
        return {"right": right, "missing_groups": missing, "extra_tables": extra}

    model = judge([_resolve(rec, c["table"]) for c in m.get("choices", [])], _rep_status)
    finals = [r for f in rec["final"].values() for r in f.get("tables") or []]
    final = judge([r["table_id"] for r in finals], _final_status)
    if ask_policy_held(case):  # an ask where the policy is undecided is neither right nor wrong
        if m.get("asks") and model["right"] is not True:
            model.update(right=None, held="ask_policy_undecided")
        if any(f.get("asks") for f in rec["final"].values()) and final["right"] is not True:
            final.update(right=None, held="ask_policy_undecided")
    std_final = case["final"]
    if std_final["status"] != "confirmed":
        final["right"] = None if final["right"] is True else final["right"]
    elif final["right"] is True:
        bad = [r["table_id"] for r in finals if std_final.get("frequency") and r["frequency"] != std_final["frequency"]]
        # months joined per statistic (an edition split keeps one statistic's tables together, another statistic never
        # fills a gap), missing, extra and repeated months all fail; ``periods`` is checked like ``period`` (.14)
        wanted = std_final.get("periods") or ([std_final["period"]] if std_final.get("period") else [])
        if wanted:
            want = sorted({x for w in wanted for x in _months(w["from"], w["to"])})
            for f in rec["final"].values():
                rows = f.get("tables") or []
                have = [x for r in rows for p in (r["period"] or []) for x in _months(p["start"], p["end"])]
                if rows and sorted(have) != want:
                    bad += [r["table_id"] for r in rows]
        if bad:
            final.update(right=False, conditions_wrong=sorted(set(bad)))
    rep_held = item_held(case, "representative")
    if rep_held:  # .22: the allowed tables are not confirmed: neither a right nor a wrong table set
        for x in (model, final):
            if x["right"] is not None:
                x.update(right=None, held=f"representative_{rep_held}")
    return {"model": model, "final": final}


def _spans(question: str, text: str) -> list[tuple[int, int]]:
    out, i = [], question.find(text)
    while text and i >= 0:
        out.append((i, i + len(text)))
        i = question.find(text, i + 1)
    return out


def _conditions(rows: list[dict], std_final: dict, titles: dict, coverage: dict | None) -> dict:
    """Months, frequency, basis and coverage of one statistic's final rows against the confirmed final."""
    d = {}
    wanted = std_final.get("periods") or ([std_final["period"]] if std_final.get("period") else [])
    if wanted and rows:
        want = sorted({x for w in wanted for x in _months(w["from"], w["to"])})
        have = [x for r in rows for p in (r["period"] or []) for x in _months(p["start"], p["end"])]
        d["period"] = "right" if sorted(have) == want else "wrong"  # gaps, extra months and overlaps fail
    if std_final.get("frequency") and rows:
        d["frequency"] = "right" if all(r["frequency"] == std_final["frequency"] for r in rows) else "wrong"
    if std_final.get("basis") and rows:
        d["basis"] = "right" if all(_basis_values(_brackets(titles.get(r["table_id"], "")), b["axis"]) &
                                    _standard_value(b["axis"], b["value"])
                                    for r in rows for b in std_final["basis"]) else "wrong"
    if coverage is not None and rows:
        inside = True
        for r in rows:
            cov = (coverage.get(r["table_id"]) or {}).get(r["frequency"])
            months = [x for p in (r["period"] or []) for x in _months(p["start"], p["end"])]
            if not cov or not months or min(months) < cov[0] or max(months) > cov[1]:
                inside = False
        d["coverage"] = "right" if inside else "wrong"
    return d


def ask_option_facts(rec: dict, case: dict, coverage: dict | None) -> list[dict]:
    """Recorded only (.20): each ask option table of the model's asks and code's final asks, whether it offers the
    confirmed frequency and covers the confirmed period (catalog coverage). Never a verdict by itself."""
    fin = case.get("final") or {}
    if fin.get("status") != "confirmed" or coverage is None:
        return []
    wanted = fin.get("periods") or ([fin["period"]] if fin.get("period") else [])
    months = sorted({x for w in wanted for x in _months(w["from"], w["to"])})
    out, seen = [], set()
    rows = [(a["for"], a["axis"], o["value"], _resolve(rec, o["table"])) for a in (rec["model"] or {}).get("asks", [])
            for o in a["options"]]
    rows += [(sid, a["axis"], o["value"], o["table_id"]) for sid, f in (rec.get("final") or {}).items()
             for a in f.get("asks") or [] for o in a["options"]]
    for sid, axis, value, t in rows:
        if not t or (sid, axis, t) in seen:
            continue
        seen.add((sid, axis, t))
        span = (coverage.get(t) or {}).get(fin.get("frequency")) if fin.get("frequency") else None
        out.append({"statistic": sid, "axis": axis, "value": value, "table": t,
                    "offers_frequency": None if not fin.get("frequency") else span is not None,
                    "covers_period": None if not (span and months) else span[0] <= months[0] and months[-1] <= span[1]})
    return out


def requirement_targets(rec: dict, case: dict, titles: dict, coverage: dict | None = None) -> dict | None:
    """Targets confirmed in the standard (``requirements``), scored per target (``METRICS``). A statistic covers a
    target when its quote, at a place it occurs in the question, holds the target's text at its place; a quote that
    occurs more than once covers what any of its places covers, unless two places cover different targets (review).
    Code-free: the scorer reads the model's quotes and tables and the final rows only."""
    req = case.get("requirements")
    if not req or req.get("status") != "confirmed":
        return None
    q, m = rec["question"] or "", rec["model"]
    targets = []
    for t in req["targets"]:
        spans = _spans(q, t["text"])
        targets.append(spans[t.get("occurrence", 1) - 1] if len(spans) >= t.get("occurrence", 1) else None)
    # .19 (user 2026-10-29 05:05 UTC): qualifiers are the question's own words that tell apart targets the text
    # alone cannot place; a statistic holds one when its own quote, or a splitter quote the model wrote for it,
    # holds it at its place. Never placed by the table it chose.
    def place(text, occurrence):
        got = _spans(q, text)
        return got[occurrence - 1] if len(got) >= occurrence else None

    quals = [[place(x["text"], x.get("occurrence", 1)) for x in t.get("qualifiers") or []] for t in req["targets"]]
    shared_span = [ts is not None and any(tj != ti and targets[tj] == ts for tj in range(len(targets)))
                   for ti, ts in enumerate(targets)]

    def holds(sid, span):
        rows = [st.get("quote") for st in m["statistics"] if st["id"] == sid] + \
            [r["quote"] for r in m.get("splitters", []) if sid in r["for"]]
        return span is not None and any(a <= span[0] and span[1] <= b for x in rows for a, b in _spans(q, x or ""))

    def held(sid, ti):
        return bool(quals[ti]) and all(holds(sid, sp) for sp in quals[ti])

    unplaced, split_review, covers, by_qualifier = [], [], {}, []
    for s in m["statistics"]:
        places = _spans(q, s.get("quote") or "")
        if not places:
            unplaced.append(s["id"])
            continue
        per_place = [frozenset(ti for ti, ts in enumerate(targets) if ts and a <= ts[0] and ts[1] <= b)
                     for a, b in places]
        nonempty = {x for x in per_place if x}
        covers[s["id"]] = set().union(*per_place) if per_place else set()
        if len(nonempty) > 1:
            # .20 (user 2026-10-29 06:20 UTC): a quote at several places is placed when the qualifiers the model
            # wrote leave targets of one place only; otherwise it stays held
            left = {frozenset(ti for ti in x if held(s["id"], ti)) for x in nonempty} - {frozenset()}
            if len(left) == 1:
                covers[s["id"]] = set(next(iter(left)))
                by_qualifier.append(s["id"])
            else:
                split_review.append(s["id"])
    # a target sharing its text with another (one item under two named tables) is covered only through its
    # qualifiers; a statistic holding that text with no qualifier of such a target is not placed (review)
    text_covers = {sid: set(c) for sid, c in covers.items()}
    qualifier_review = []
    for sid, c in text_covers.items():
        keep = {ti for ti in c if not shared_span[ti] or not quals[ti] or held(sid, ti)}
        needed = [ti for ti in c if shared_span[ti] and quals[ti]]
        if needed and not keep & set(needed):
            qualifier_review.append(sid)
        covers[sid] = keep
    # a statistic placed on no target leaves a target no statistic covers held, not missing: the target may be
    # what that statistic means (named or evidenced but not quoted), which the quotes cannot place (.19)
    placed_nowhere = [sid for sid, c in covers.items() if not c and sid not in qualifier_review]
    std_final = case["final"]
    rows_of = {sid: (rec["final"].get(sid) or {}).get("tables") or [] for sid in covers}
    out = []
    for ti, t in enumerate(req["targets"]):
        allowed = set(t["tables"])
        sids = [sid for sid, c in covers.items() if ti in c]
        row = {"target": t["id"], "text": t["text"], "statistics": sids}
        unqualified = [sid for sid in qualifier_review if ti in text_covers[sid]]
        if unqualified:
            row["unqualified_statistics"] = unqualified
        if len(sids) > 1:
            row["covered_more_than_once"] = True  # recorded only: several statistics on one target are allowed
        if targets[ti] is None:
            row.update(model="review", tables="review", full="review", why="target text not in the question")
        elif not sids:
            mark = "review" if unplaced or split_review or unqualified or placed_nowhere else "missing"
            row.update(model=mark, tables=mark, full=mark)
            if unqualified:
                row["why"] = "a statistic holds the target text without a qualifier: not placed"
            elif placed_nowhere and not (unplaced or split_review):
                row["why"] = f"statistics {placed_nowhere} are placed on no target: not decidable from the quotes"
        elif any(sid in split_review for sid in sids):
            row.update(model="review", tables="review", full="review", why="a quote placed on different targets")
        else:
            reps = [[_resolve(rec, c["table"]) for c in m["choices"] if sid in c["for"]] for sid in sids]
            row["model"] = "right" if all(r and set(r) <= allowed for r in reps) else "wrong"
            rows = [r for sid in sids for r in rows_of[sid]]
            tab = "right" if all(rows_of[sid] and {r["table_id"] for r in rows_of[sid]} <= allowed
                                 for sid in sids) else "wrong"
            row["tables"] = tab
            if std_final["status"] == "confirmed":
                conds = {}
                for sid in sids:
                    for k, v in _conditions(rows_of[sid], std_final, titles, coverage).items():
                        conds[k] = "wrong" if v == "wrong" or conds.get(k) == "wrong" else v
                row["conditions"] = conds
                row["full"] = "wrong" if tab == "wrong" or "wrong" in conds.values() else "right"
            else:
                row["full"] = "na"
        out.append(row)
    uncovered = [sid for sid, c in covers.items() if not c]
    return {"targets": out, "unplaced_statistics": unplaced, "uncovered_statistics": uncovered,
            "split_review": split_review, "qualifier_review": qualifier_review,
            "placed_by_qualifier": by_qualifier, "row_count": "not forced",
            "limit": "a series left out inside an allowed table is not detectable"}


def ask_measures(rec: dict, case: dict, titles: dict) -> dict:
    """The fixed ask measures of one question (user 2026-10-28 07:03 UTC; definitions in ``METRICS``). A held policy
    makes the ask counts ``review``; an answer to a contract without option evidence makes the evidence count
    ``not_measurable`` (never a violation of the later contract)."""
    m = rec.get("model") or {"asks": [], "statistics": []}
    held = sorted(sid for sid, c in rec["checks"].items()
                  if any(r["code"] in ("basis_axis_held", "target_table_unconfirmed") for r in c["reasons"]))
    out = {"held_statistics": held, "answer_contract": rec.get("answer_contract")}
    policy_review = ask_policy_held(case)
    expected = case["ask"]["expected"]
    axis = case["ask"].get("axis") if expected == "required" else None
    asked = [(a["for"], a["axis"]) for a in m["asks"]]
    if policy_review:
        out.update(ask_missing="review", ask_unneeded="review", option_meaning_errors="review")
    else:
        out["ask_missing"] = [] if axis is None or any(x == axis for _, x in asked) else [["question", axis]]
        out["ask_unneeded"] = [list(k) for k in asked if k[1] != axis]
        errors = []
        exp = _expected_ask(case, titles) if axis else None
        if exp is not None:
            seen = set()
            for a in m["asks"]:
                if a["axis"] != axis:
                    continue
                for o in a["options"]:
                    table = _resolve(rec, o["table"])
                    v = _table_value(titles, table, axis)
                    named = _written_value(axis, o["value"])
                    seen.add(v)
                    if v not in exp["values"]:
                        errors.append([a["for"], o["value"], "value_not_expected"])
                    elif named not in v or table not in exp["values"][v]:
                        errors.append([a["for"], o["value"], "table_not_allowed_for_value"])
            if any(x == axis for _, x in asked):
                errors += [["question", "/".join(sorted(v)), "value_left_out"] for v in exp["values"] if v not in seen]
        out["option_meaning_errors"] = errors if exp is not None or not axis else "review"
    if rec.get("answer_contract") not in (None, *OPTION_EVIDENCE_CONTRACTS) and m["asks"]:
        out["option_evidence_errors"] = "not_measurable"
    else:
        ev = []
        for a in m["asks"]:
            for o in a["options"]:
                table = _resolve(rec, o["table"])
                if table is None:
                    continue
                bad = [e["text"] for e in o.get("evidence", []) if _evidence_error(rec, table, e) is not None]
                if bad or not o.get("evidence"):
                    ev.append([a["for"], a["axis"], o["value"], bad or "no_evidence"])
        out["option_evidence_errors"] = ev
    return out


def _evidence_error(rec: dict, table: str, ev: dict) -> str | None:
    kinds = _fact_kinds(rec, table, ev["text"])
    if kinds.get(ev["kind"]) == 1:
        return None
    if ev["kind"] in kinds:
        return "evidence_normalization_ambiguous"
    if kinds:
        return "evidence_kind_mismatch"
    if any(_fact_kinds(rec, t, ev["text"]) for t in rec["pool"] if t != table):
        return "evidence_of_other_table"
    return "evidence_not_on_card"


def model_facts(rec: dict, titles: dict | None = None) -> dict[str, set[str]]:
    """The scorer's own check of the model output: statistic id -> fact error codes."""
    m, q = rec["model"], rec["question"]
    errors: dict[str, set[str]] = {s["id"]: set() for s in m["statistics"]}
    for s in m["statistics"]:
        if _ns(s["quote"]) not in _ns(q):
            errors[s["id"]].add("quote_not_in_question")
    for key in ("splitters", "periods", "frequencies"):
        for row in m[key]:
            if _ns(row["quote"]) not in _ns(q):
                for sid in row["for"]:
                    errors.setdefault(sid, set()).add("quote_not_in_question")
    for c in m["choices"]:
        table = _resolve(rec, c["table"])
        for sid in c["for"]:
            if table is None:
                errors.setdefault(sid, set()).add("table_not_in_pool")
                continue
            for ev in c["evidence"]:
                if (code := _evidence_error(rec, table, ev)) is not None:
                    errors[sid].add(code)
    for a in m["asks"]:  # contract .3: the model's evidence per option table
        for o in a["options"]:
            table = _resolve(rec, o["table"])
            for ev in o.get("evidence", []) if table is not None else []:
                if (code := _evidence_error(rec, table, ev)) is not None:
                    errors.setdefault(a["for"], set()).add(code)
    for (sid, _axis), probs in ask_facts(rec, titles)[0].items():
        if probs:
            errors.setdefault(sid, set()).add("ask_invalid")
    return errors


def _period_support(quote: str) -> str:
    if any(p.search(quote) for p in UNSUPPORTED_PERIOD_FORMS):
        return "unsupported"
    if any(p.fullmatch(quote.strip()) for p in SUPPORTED_PERIOD_FORMS):
        return "supported"
    return "unknown"


def _months(start: str, end: str) -> list[int]:
    def first(s):
        y = int(s[:4])
        return y * 12 + ((int(s[-1]) - 1) * 3 if "Q" in s else int(s[5:7]) - 1 if len(s) >= 7 else 0)

    def last(s):
        y = int(s[:4])
        return y * 12 + (int(s[-1]) * 3 - 1 if "Q" in s else int(s[5:7]) - 1 if len(s) >= 7 else 11)

    return list(range(first(start), last(end) + 1))


# ---------------------------------------------------------------- the standard's ranges


def _rep_status(case: dict, table: str | None, gi: int) -> str:
    rep = case["representative"]
    group = rep["confirmed_groups"][gi]
    if table is None:
        return "none"
    if table in group or any(table in (rep.get("confirmed_equivalents") or {}).get(t, []) for t in group):
        return "right"
    if any(table in (rep.get("proposed_equivalents") or {}).get(t, []) for t in group):
        return "review"
    return "other"


def _final_status(case: dict, table: str, gi: int) -> str:
    groups = case["final"].get("groups") or case["representative"]["confirmed_groups"]
    if gi >= len(groups):
        return "other"
    if table in groups[gi]:
        return "right"
    if any(table in (case["representative"].get("proposed_equivalents") or {}).get(t, []) for t in groups[gi]):
        return "review"
    return "other"


def _basis_values(text: str, axis: str) -> set[str]:
    from claview_a.search.basis import values as basis_values

    return set(basis_values(text, axis))


def _brackets(title: str) -> str:
    m = re.search(r"\((.*)\)", title or "")
    return m.group(1) if m else ""


def _table_value(titles: dict, table: str | None, axis: str) -> frozenset:
    return frozenset(_basis_values(_brackets(titles.get(table or "", "")), axis))


def _standard_value(axis: str, value: str) -> set[str]:
    """The axis values a standard's basis value names, for comparing with a table's (user 2026-10-28 06:27 UTC):
    the current path's reading of the value, else the shared explicit forms of that axis (loan_basis '잔액' ->
    '잔액기준'), else the value as written. Only the standard's per-axis value; no question text is read here."""
    return _basis_values(value, axis) or {_written_value(axis, value) or value}


def _written_value(axis: str, written: str) -> str | None:
    """The axis value an option's written value names under the shared explicit forms; the written value itself when
    no form lists it; None when two values claim it."""
    hit = [v for v, forms in VALUE_FORMS.get(axis, {}).items() if _ns(written) in {_ns(f) for f in forms}]
    return written if not hit else hit[0] if len(hit) == 1 else None


def _expected_ask(case: dict, titles: dict) -> dict | None:
    """{axis, values: {value: allowed tables}} from the standard, or None when the standard gives no options. A value
    is the axis value written in its table's brackets (so '잔액' and '잔액기준' are one value)."""
    ask = case["ask"]
    axis = ask.get("axis")
    tables = list((ask.get("options") or {}).values()) or list(ask.get("tables") or [])
    if not axis or not tables:
        return None
    out: dict[frozenset, set[str]] = {}
    for table in tables:
        v = _table_value(titles, table, axis)
        if not v:
            return None
        out.setdefault(v, set()).add(table)
    eq = case["representative"].get("confirmed_equivalents") or {}
    return {"axis": axis, "values": {v: ts | {x for t in ts for x in eq.get(t, [])} for v, ts in out.items()}}


def _score_ask(asks: list[tuple[str, dict]], expected: dict, table_of, titles: dict) -> tuple[bool, list]:
    """asks: [(statistic, {axis, options:[{value, table}]})]. True when the expected axis is asked, at most once per
    statistic (.15, user 2026-10-28 19:51 UTC: split statistics may each ask the axis they need; one statistic asking
    the same axis twice is still wrong), no other axis is asked, and every ask on the axis has each option name the
    value its table carries, the expected values exactly once each, and each option table allowed for its value."""
    problems = []
    axes = {a["axis"] for _, a in asks}
    on_axis = [(sid, a) for sid, a in asks if a["axis"] == expected["axis"]]
    if not on_axis:
        problems.append("asks on the expected axis: 0")
    per_stat = [sid for sid, _ in on_axis]
    repeated = sorted({sid for sid in per_stat if per_stat.count(sid) > 1})
    if repeated:
        problems.append(f"the expected axis asked more than once on statistic {repeated}")
    if axes - {expected["axis"]}:
        problems.append(f"extra axes: {sorted(axes - {expected['axis']})}")
    for sid, a in on_axis:
        got: dict[frozenset, list[str]] = {}
        for o in a["options"]:
            table = table_of(o)
            v = _table_value(titles, table, expected["axis"])
            named = _written_value(expected["axis"], o["value"])
            if not v or named not in v:
                problems.append(f"{sid}: option {o['value']!r} does not match its table {table}")
            got.setdefault(v, []).append(table)
        if set(got) != set(expected["values"]):
            problems.append(f"{sid}: values {sorted(map(sorted, got))} != {sorted(map(sorted, expected['values']))}")
        for v, ts in got.items():
            if len(ts) != 1:
                problems.append(f"{sid}: value {sorted(v)} given {len(ts)} times")
            if v in expected["values"] and not set(ts) <= expected["values"][v]:
                problems.append(f"{sid}: value {sorted(v)} table {ts} not allowed")
    return not problems, problems


# ---------------------------------------------------------------- scoring


def fact_checks(rec: dict, titles: dict, res: dict) -> tuple[dict, dict]:
    """The scorer's own fact check of the model output against code's checks (``contract``, ``evidence_contract``,
    ``code_checks`` and their labels, written into ``res``); shared by every standard's scoring. Returns the fact
    errors per statistic and code's handling per statistic."""
    # model output facts (the scorer's own) against code's checks
    facts = model_facts(rec, titles)
    ask_undecided = ask_facts(rec, titles)[1]
    # error by error (review 2026-10-28 05:58 UTC): the scorer's errors against code's, by statistic, group, target
    found_items, undecided_items = fact_items(rec, titles)
    caught_items: dict[tuple, str] = {}
    for sid, c in rec["checks"].items():
        for where, rows in (("reject", c["reasons"]), ("record", c.get("citation", []))):
            for r in rows:
                if r["code"] in MODEL_FACT_CODES:
                    caught_items[(sid, _group(r["code"]), _code_key(r["code"], r.get("detail")))] = where
    items = {"caught": sorted(map(list, found_items & set(caught_items))),
             "missed": sorted(map(list, found_items - set(caught_items))),
             "false_reject": [], "false_report": [], "review": []}
    for item, where in caught_items.items():
        if item in found_items:
            continue
        if item in undecided_items:
            items["review"].append(list(item))
            res["review"].append({"what": "ask rejection not decidable", "statistic": item[0],
                                  "why": ask_undecided.get((item[0], item[2]))})
        else:
            items["false_reject" if where == "reject" else "false_report"].append(list(item))
    checks = {}
    for sid in {x[0] for x in found_items} | {x[0] for x in caught_items}:
        mine = {k for k, rows in items.items() if any(x[0] == sid for x in rows)}
        checks[sid] = ("code_check_missed" if "missed" in mine else "code_false_reject" if "false_reject" in mine
                       else "code_false_report" if "false_report" in mine else "code_check_review" if "review" in mine
                       else "code_check_success")
    for sid, c in rec["checks"].items():
        for r in c["reasons"]:
            if r["code"] in PERIOD_STOP_CODES:
                quote = (r.get("detail") or {}).get("quote") if isinstance(r.get("detail"), dict) else None
                kind = _period_support(quote) if quote else "unknown"
                if kind == "supported":
                    checks[sid] = "code_false_reject"
                elif kind == "unsupported":
                    res["labels"].append("unsupported_expression")
                else:
                    res["review"].append({"what": "period stop not decidable", "statistic": sid, "quote": quote})
    res["contract"] = {"ok": not any(facts.values()), "model_fact_errors": {k: sorted(v) for k, v in facts.items()
                                                                            if v}, "checks": checks,
                       "check_items": items}
    res["evidence_contract"] = {"ok": not any(v & set(CITATION_FACT_CODES) for v in facts.values())}
    vals = set(checks.values())
    res["code_checks"] = {"right": False if vals & {"code_check_missed", "code_false_reject", "code_false_report"} else
                          None if "code_check_review" in vals else True}
    if any(facts.values()):
        res["labels"].append("model_output_error")
    for lab in sorted(set(checks.values())):
        res["labels"].append(lab)
    return facts, checks


def score_case(rec: dict, case: dict, titles: dict, coverage: dict | None = None) -> dict:
    """One question. ``titles``: table -> title; ``coverage``: table -> {frequency: (first month, last month)} from the
    catalog metadata (optional: without it the per-table coverage check is not applicable)."""
    rep_groups = case["representative"]["confirmed_groups"]
    std_final = case["final"]
    ask = case["ask"]
    policy_review = ask_policy_held(case)  # only the ask part is held
    res = {"case_id": case["case_id"], "part": case["part"], "labels": [], "review": []}

    # run information as the run recorded it
    info = rec.get("run_info") or {}
    res["run"] = {"verified": bool(info.get("versions")) and info.get("as_of") == rec.get("as_of") and bool(rec.get(
        "as_of")), "info": info}

    # pool: confirmed, proposed only, or missing
    pool = set(rec["pool"])
    hits = []
    for gi, g in enumerate(rep_groups):
        eq_c = {x for t in g for x in (case["representative"].get("confirmed_equivalents") or {}).get(t, [])}
        eq_p = {x for t in g for x in (case["representative"].get("proposed_equivalents") or {}).get(t, [])}
        hits.append(True if (set(g) | eq_c) & pool else None if eq_p & pool else False)
    res["pool"] = {"hit": (False if False in hits else None if None in hits else True) if rep_groups else "na",
                   "groups": hits}
    if False in hits:
        res["labels"].append("retrieval_miss")

    if rec["error"]:
        res["labels"].append(f"run_error:{rec['error']['stage']}:{rec['error']['code']}")
        res.update(contract={"ok": False}, evidence_contract={"ok": False}, code_checks={"right": None},
                   completed={"right": False if std_final["status"] == "confirmed" else "na"},
                   model_meaning={"right": False}, table_match={"right": False},
                   final_full={"right": False if std_final["status"] == "confirmed" else "na"}, effects={})
        return res
    m = rec["model"]

    facts, checks = fact_checks(rec, titles, res)
    conditions = condition_details(rec)  # .18: by statistic, condition and quote
    extraction = conditions["summary"]
    cond_problems = condition_problems(conditions)
    res["extraction"], res["condition_detail"] = extraction, conditions
    res["condition_counts"] = condition_counts(conditions)
    for state, label in (("missing", "model_extraction_missing"), ("other_field", "condition_in_other_field"),
                         ("not_kept", "condition_not_kept")):
        if any(x["state"] == state for x in cond_problems):
            res["labels"].append(label)

    # statistics to groups, by table only
    resolve = lambda o: _resolve(rec, o["table"])  # noqa: E731
    stat_tables: dict[str, dict] = {}
    for s in m["statistics"]:
        sid = s["id"]
        rep = [_resolve(rec, c["table"]) for c in m["choices"] if sid in c["for"]]
        opts = [resolve(o) for a in m["asks"] if a["for"] == sid for o in a["options"]]
        fin = [t["table_id"] for t in (rec["final"].get(sid) or {}).get("tables") or []]
        fin_opts = [o["table_id"] for a in (rec["final"].get(sid) or {}).get("asks") or [] for o in a["options"]]
        stat_tables[sid] = {"rep": rep, "opts": opts, "final": fin, "final_opts": fin_opts}
    mapping, unmapped, ambiguous = {}, [], []
    for sid, ts in stat_tables.items():
        alltabs = [t for k in ("rep", "opts", "final", "final_opts") for t in ts[k] if t]
        gis = sorted({gi for gi in range(len(rep_groups)) for t in alltabs
                      if _rep_status(case, t, gi) in ("right", "review") or _final_status(case, t, gi) in ("right",
                                                                                                         "review")})
        if len(gis) == 1:
            mapping[sid] = gis[0]
        elif not gis:
            unmapped.append(sid)
        else:
            ambiguous.append(sid)
    per_group = {gi: [sid for sid, g in mapping.items() if g == gi] for gi in range(len(rep_groups))}
    # several statistics on one group's tables, or a statistic on several groups: the table cannot tell which
    # statistic is which, so a group left without one is held, never counted wrong (user 2026-10-28 04:36 UTC)
    mapping_uncertain = bool(ambiguous) or any(len(v) > 1 for v in per_group.values())
    res["mapping"] = {"statistic_to_group": mapping, "unmapped": unmapped, "ambiguous": ambiguous,
                      "groups": per_group}
    stats_confirmed = case["statistics"]["status"] == "confirmed"

    expected = ask["expected"]
    exp_ask = _expected_ask(case, titles) if expected == "required" else None
    model_asks = [(a["for"], {"axis": a["axis"], "options": a["options"]}) for a in m["asks"]]

    # .20 (user 2026-10-29 06:20 UTC): where the ask policy is held, a statistic that asked (its options outside the
    # answer groups) leaves a group without a table: held with the policy, never "missing"
    asked_unmapped = [sid for sid in unmapped if stat_tables[sid]["opts"] and not stat_tables[sid]["rep"]]
    held_by_policy = policy_review and bool(asked_unmapped)
    res["ask_option_facts"] = ask_option_facts(rec, case, coverage)

    # model meaning per group
    meaning = []
    for gi in range(len(rep_groups)):
        sids = per_group[gi]
        if len(sids) != 1:
            meaning.append("review" if sids or mapping_uncertain or held_by_policy else "missing")
            continue
        sid = sids[0]
        if not stat_tables[sid]["rep"] and stat_tables[sid]["opts"] and expected == "required":
            # an ask-only statistic where an ask is expected: scored by its own ask against the standard's ask
            if policy_review or exp_ask is None:
                meaning.append("review")
            else:
                meaning.append("right" if _score_ask([x for x in model_asks if x[0] == sid], exp_ask, resolve,
                                                     titles)[0] else "wrong")
            continue
        st = [_rep_status(case, t, gi) for t in stat_tables[sid]["rep"]] or \
            [_rep_status(case, t, gi) for t in stat_tables[sid]["opts"]]
        meaning.append("right" if st and all(x == "right" for x in st) else "review" if "review" in st else "wrong")
    rep_held = item_held(case, "representative")
    if rep_held:  # .22: allowed tables not confirmed: a group's table is held, never right, wrong or missing
        meaning = ["review" if x in ("right", "wrong", "missing") else x for x in meaning]
    req = requirement_targets(rec, case, titles, coverage)
    if req is not None:
        meaning = [t["model"] for t in req["targets"]] + ["review" for _ in req["uncovered_statistics"]]
    res["requirements"] = req
    if policy_review:
        model_ask = "review"
    elif expected == "required":
        if exp_ask is None:
            model_ask = "review"
        else:
            ok, _ = _score_ask(model_asks, exp_ask, resolve, titles)
            model_ask = "right" if ok else "wrong"
    elif expected in ("not_expected", "policy_no_ask"):
        model_ask = "wrong" if model_asks else "right"
    else:
        model_ask = "na"
    missing_wrong = bool(m["missing"]) and False not in hits
    parts = meaning + ([model_ask] if model_ask != "na" else [])
    if missing_wrong:
        parts.append("wrong")
    if expected == "required" and not rep_groups:
        parts = [model_ask]
    res["model_meaning"] = {"groups": meaning, "ask": model_ask, "missing_wrong": missing_wrong,
                            "right": False if ("wrong" in parts or "missing" in parts) else None if "review" in parts
                            else True}
    if res["model_meaning"]["right"] is False:
        res["labels"].append("model_meaning_wrong")

    # final per group (tables, months inside the statistic, frequency, basis) and the ask
    unverified = sorted(sid for sid, f in rec["final"].items() if f["outcome"].endswith("_unverified"))
    outcomes = {sid: f["outcome"].removesuffix("_unverified") for sid, f in rec["final"].items()}
    final_asks = [(sid, a) for sid, f in rec["final"].items() for a in f["asks"]]
    table_parts, full_parts, detail = [], [], []
    if req is not None:
        for t in req["targets"]:
            table_parts.append(t["tables"])
            full_parts.append(t["full"])
            detail.append({k: t[k] for k in ("target", "statistics", "tables", "full", "conditions") if k in t})
    for gi in range(len(rep_groups)):
        sids = per_group[gi]
        if (expected == "required" and not policy_review) or req is not None:
            break
        if len(sids) != 1:
            mark = "review" if sids or mapping_uncertain or held_by_policy or rep_held else "missing"
            table_parts.append(mark)
            full_parts.append(mark)
            detail.append({"group": gi, "status": "held with the ask policy" if held_by_policy and not sids else
                           "mapping held" if mark == "review" else "no statistic"})
            continue
        sid = sids[0]
        rows = (rec["final"].get(sid) or {}).get("tables") or []
        tstat = [_final_status(case, r["table_id"], gi) for r in rows]
        if not rows and outcomes.get(sid) == "ask":
            t_ok = "review" if policy_review else "wrong"  # an ask where a table is expected
        else:
            t_ok = "wrong" if not rows or "other" in tstat else "review" if "review" in tstat else "right"
        if rep_held and t_ok in ("right", "wrong"):
            t_ok = "review"  # .22: the period and frequency below are still scored when confirmed
        table_parts.append(t_ok)
        d = {"group": gi, "statistic": sid, "tables": t_ok}
        if std_final["status"] == "confirmed":
            d.update(_conditions(rows, std_final, titles, coverage))
            checks_ = [d.get(k) for k in ("period", "frequency", "basis", "coverage") if d.get(k)]
            full_parts.append("wrong" if t_ok == "wrong" or "wrong" in checks_ else
                              "review" if t_ok == "review" else "right")
            if t_ok == "review" and not rows:
                full_parts[-1] = "review"
        else:
            full_parts.append("na")
        if outcomes.get(sid) != "selected":
            d["outcome"] = outcomes.get(sid)
        detail.append(d)
    ask_final = None
    if expected == "required" and not policy_review:
        if exp_ask is None:
            ask_final = "review"
        else:
            ok, problems = _score_ask(final_asks, exp_ask, lambda o: o.get("table_id"), titles)
            others_bad = [sid for sid, o in outcomes.items() if o == "failed"]
            # .16: with one answer group, a statistic that hands off a table where the ask is due skipped the ask
            selected_instead = [sid for sid, o in outcomes.items() if o == "selected"] if len(rep_groups) <= 1 else []
            ask_final = "right" if ok and not others_bad and not selected_instead else "wrong"
            detail.append({"ask": ask_final, "problems": problems, "failed_statistics": others_bad,
                           "selected_where_ask_due": selected_instead})
        table_parts, full_parts = [ask_final], [ask_final]
    elif policy_review:
        ask_final = "review"
        full_parts.append("review")  # tables still scored: only whether an ask was due is held
    elif expected in ("not_expected", "policy_no_ask") and final_asks:
        ask_final = "wrong"
        table_parts.append("wrong")
        full_parts.append("wrong")
    ask_sids = {sid for sid, a in final_asks + model_asks if expected == "required" and a["axis"] == ask.get("axis")}
    extras = [sid for sid in unmapped + ambiguous if outcomes.get(sid) in ("selected", "ask", "failed")
              and sid not in ask_sids]  # the statistic an expected ask is about is not an extra one
    if req is not None:  # a statistic covering no target is held: the row count is never forced
        extras = [sid for sid in req["uncovered_statistics"] if sid not in ask_sids]
    if extras and req is not None:
        table_parts.append("review")
        full_parts.append("review")
        detail.append({"extra_statistics": extras, "status": "review"})
    elif extras:
        mark = "wrong" if stats_confirmed else "review"
        table_parts.append(mark)
        full_parts.append(mark)
        detail.append({"extra_statistics": extras, "status": mark})

    def verdict(parts_):
        if not parts_:
            return "na"
        if "wrong" in parts_ or "missing" in parts_:
            return False
        if "review" in parts_:
            return None
        if all(p == "na" for p in parts_):
            return "na"
        return True

    res["table_match"] = {"right": verdict(table_parts)}
    res["final_full"] = {"right": verdict(full_parts) if std_final["status"] == "confirmed" or (
        expected == "required" and not policy_review) else "na"}
    # complete only when the scorer's own checks agree (review 2026-10-28 05:58 UTC), not on code's state names alone
    ff = res["final_full"]["right"]
    blocked = [w for w, bad in (("scorer_fact_errors", not res["contract"]["ok"]),
                                ("scorer_evidence_errors", not res["evidence_contract"]["ok"]),
                                ("evidence_unverified", bool(unverified)),
                                ("route_not_verified_handoff", rec.get("route") not in ("handoff", "ask"))) if bad]
    # .16 (user 2026-10-28 19:56 UTC): a condition that surely failed makes completion wrong even when the
    # per-statistic selection is held (a fact or evidence error, unverified evidence, a failed route, or a final table
    # set that misses a group or adds a table); held only when nothing failed and something could not be decided
    sets = table_sets(rec, case)
    if sets["final"]["right"] is False and sets["final"].get("held") is None:
        blocked.append("final_table_set_wrong")
    if ff is False or blocked:
        done = False
    elif ff is True:
        done = None if res["code_checks"]["right"] is None else True
    else:
        done = ff
    res["completed"] = {"right": done, "evidence_unverified": unverified, "blocked_by": blocked}
    if unverified:
        res["labels"].append("selected_evidence_unverified")
    res["final_detail"] = detail
    res["final_ask"] = ask_final

    # effects item by item (same item before and after) and code causes
    effects = {}
    for gi, d in enumerate(x for x in detail if "group" in x and "statistic" in x):
        before = meaning[d["group"]] if d["group"] < len(meaning) else None
        after = d["tables"]
        effects[f"group{d['group']}:table"] = _effect(before, after)
    for ti, t in enumerate((req or {}).get("targets", [])):
        effects[f"{t['target']}:table"] = _effect(t["model"], t["tables"])
    if expected == "required" and not policy_review:
        effects["ask"] = _effect(model_ask, ask_final)
    res["effects"] = effects
    corrections = [c for cs in rec["corrections"].values() for c in cs]
    # .17: a condition quoted nowhere, only in another field, or unclear is the model's side (or unknown), so a
    # worse final result is not put on the code; an end point the code did not keep stays on the code. .18: only
    # the conditions of the item's own statistics count (one not attributable to any statistic counts for all)
    model_side = [x for x in cond_problems if x["state"] in ("missing", "other_field", "review")]

    def model_clean(sids) -> bool:
        return not any(facts.values()) and not any(
            x["statistics"] is None or set(x["statistics"]) & set(sids) for x in model_side)

    item_sids = {f"group{d['group']}:table": [d["statistic"]] for d in detail if "group" in d and "statistic" in d}
    item_sids.update({f"{t['target']}:table": list(t.get("statistics") or []) for t in (req or {}).get("targets", [])})
    every = list(rec["final"])
    for item, eff in effects.items():
        if eff == "worsened":
            res["labels"].append("code_correction_error" if model_clean(item_sids.get(item, every)) else
                                 "cause_review")
        if eff == "improved" and corrections:
            res["labels"].append("code_correction_success")
    if res["model_meaning"]["right"] is True and res["final_full"]["right"] is False and not effects_worse(effects) \
            and model_clean(every) and "code_false_reject" not in checks.values() and "unsupported_expression" not in \
            res["labels"]:
        res["labels"].append("code_correction_error")
    res["ask_measures"] = ask_measures(rec, case, titles)
    sets = table_sets(rec, case)
    res["model_table_set"], res["final_table_set"] = sets["model"], sets["final"]
    if case.get("expectation_items"):  # .22: per-item verdicts and their effect on completion
        res["items"] = item_verdicts(case, res, model_ask, detail)
        held = [k for k, v in res["items"].items() if v["verdict"] == "held"]
        # completion stays about the final result: only the final side's confirmed items can fail it here
        wrong = [k for k in ("period_frequency", "final_tables") if res["items"][k]["verdict"] == "wrong"]
        done = res["completed"]
        if wrong and done["right"] is not False:
            done.update(right=False, wrong_items=wrong)
        elif held and done["right"] in (True, "na") and not done["blocked_by"]:
            done.update(right=None, held_items=held)
    res["diagnostics"] = rec["diagnostics"]  # recorded only; never read above
    res["labels"] = list(dict.fromkeys(res["labels"]))
    return res


def item_verdicts(case: dict, res: dict, model_ask: str, detail: list[dict]) -> dict:
    """.22: one verdict per item the standard writes in ``expectation_items``: right / wrong / review / na,
    or ``held`` for an unconfirmed item (never counted right or wrong). The representative is the model's table set
    against the allowed groups, the ask is the model's ask against the expected one, the period and frequency are the
    final rows' months and frequency; ``final_tables`` (editions and extra tables of the final rows) is derived."""
    def mark(x) -> str:
        return {True: "right", False: "wrong", None: "review"}.get(x, "na")

    out = {}
    for item in ("representative", "ask", "period_frequency"):
        state = ((case.get("expectation_items") or {}).get(item) or {}).get("state")
        if item_held(case, item) or (item == "period_frequency" and case["final"]["status"] != "confirmed"):
            out[item] = {"state": state, "verdict": "held"}
            continue
        if item == "representative":
            verdict = mark(res["model_table_set"]["right"])
        elif item == "ask":
            verdict = model_ask if model_ask in ("right", "wrong", "review") else "na"
        else:
            marks = [d[k] for d in detail for k in ("period", "frequency") if d.get(k)]
            verdict = "wrong" if "wrong" in marks else "right" if marks else "na"
        out[item] = {"state": state, "verdict": verdict}
    out["final_tables"] = {"state": "derived", "verdict": mark(res["final_table_set"]["right"])}
    return out


def effects_worse(effects: dict) -> bool:
    return any(v == "worsened" for v in effects.values())


def _effect(before, after) -> str:
    if before in (None, "review", "na", "missing") or after in (None, "review", "na"):
        return "not_comparable"
    if before != "right" and after == "right":
        return "improved"
    if before == "right" and after != "right":
        return "worsened"
    return "same"


def attribute(res: dict) -> list[str]:
    """Where the errors happened (several can hold at once; never summed into one error count)."""
    return list(res["labels"])


def _ask_totals(rows: list[dict]) -> dict:
    """Summed ask measures and the questions with any (``review`` and ``not_measurable`` kept apart)."""
    out = {}
    for key in ("held_statistics", "ask_missing", "ask_unneeded", "option_meaning_errors", "option_evidence_errors"):
        vals = [(r["case_id"], (r.get("ask_measures") or {}).get(key)) for r in rows]
        lists = [(c, v) for c, v in vals if isinstance(v, list)]
        out[key] = {"count": sum(len(v) for _, v in lists), "questions": sorted(c for c, v in lists if v),
                    "review": sorted(c for c, v in vals if v == "review"),
                    "not_measurable": sorted(c for c, v in vals if v == "not_measurable")}
    return out


def _targets_right(res: dict):
    """One question's requirement targets: right when every target is right on the model and final sides, else
    False on a wrong or missing one, None on review; na without confirmed targets."""
    req = res.get("requirements")
    if not req:
        return "na"
    marks = [t[k] for t in req["targets"] for k in ("model", "full") if t.get(k) not in (None, "na")]
    marks += ["review" for _ in req["uncovered_statistics"]]
    return False if {"wrong", "missing"} & set(marks) else None if "review" in marks else True


def aggregate(results: list[dict]) -> dict:
    out = {}
    for part in PARTS:
        rows = [r for r in results if part == "all" or r["part"] == part]

        def rate(get):
            vals = [get(r) for r in rows]
            right = sum(1 for v in vals if v is True)
            wrong = sum(1 for v in vals if v is False)
            review = sum(1 for v in vals if v is None)
            return {"right": right, "wrong": wrong, "review": review, "na": len(vals) - right - wrong - review,
                    "denominator": right + wrong}

        labels: dict[str, int] = {}
        for r in rows:
            for lab in attribute(r):
                labels[lab] = labels.get(lab, 0) + 1
        effects: dict[str, int] = {}
        for r in rows:
            for v in (r.get("effects") or {}).values():
                effects[v] = effects.get(v, 0) + 1
        out[part] = {
            "questions": len(rows),
            "run_verified": rate(lambda r: r["run"]["verified"]),
            "pool_hit": rate(lambda r: r["pool"]["hit"]),
            "contract_ok": rate(lambda r: (r.get("contract") or {}).get("ok")),
            "model_meaning_right": rate(lambda r: (r.get("model_meaning") or {}).get("right")),
            "table_match": rate(lambda r: (r.get("table_match") or {}).get("right")),
            "evidence_contract_ok": rate(lambda r: (r.get("evidence_contract") or {}).get("ok")),
            "code_checks_right": rate(lambda r: (r.get("code_checks") or {}).get("right")),
            "completed": rate(lambda r: (r.get("completed") or {}).get("right")),
            "model_table_set": rate(lambda r: (r.get("model_table_set") or {}).get("right")),
            "final_table_set": rate(lambda r: (r.get("final_table_set") or {}).get("right")),
            "requirement_targets": rate(lambda r: _targets_right(r)),
            "final_full": rate(lambda r: (r.get("final_full") or {}).get("right")),
            "effects": effects, "labels": labels,
            "review": [r["case_id"] for r in rows if r["review"]],
            "ask_measures": _ask_totals(rows),
        }
        item_rows = [r for r in rows if r.get("items")]
        if item_rows:  # .22: denominators per expectation item (held never counted)
            out[part]["items"] = {
                k: {v: sum(1 for r in item_rows if r["items"][k]["verdict"] == v)
                    for v in ("right", "wrong", "review", "na", "held")}
                for k in ("representative", "ask", "period_frequency", "final_tables")}
    return out
