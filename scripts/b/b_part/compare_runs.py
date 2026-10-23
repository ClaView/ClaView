"""Side-by-side latency / token / cost table from verify_expand_hcx.py result files.

No network, no key. Reads the JSON files the verify script wrote (they must come from
a run made with the latency/token recording; older files have no per-call numbers and
are reported as "not measured"). Prices are applied here, after the fact, so the same
files can be re-priced without calling the models again.

Usage (Windows PowerShell, from the project root):
    python scripts/b/b_part/compare_runs.py `
        HCX=eval/results/lat_hcx_g22.json GPT=eval/results/lat_gpt_g22.json `
        --price HCX=1.0,4.0 --price GPT=2.5,10.0 --currency USD

  LABEL=PATH          one result file (repeat for several; files with the same LABEL are
                      pooled, e.g. HCX=g22.json HCX=new10.json)
  --price LABEL=IN,OUT  price per 1,000,000 input / output tokens for that label
  --exclude CASE ...  leave these case ids out (e.g. --exclude CV-NEW-07)
  --markdown PATH     also write the table as a markdown file

Latency is the last HTTP attempt of each call (request sent -> response received);
`--delay` pauses and 429 back-offs are not counted. Costs are only as good as the prices
you pass in.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_metrics import money, seconds, summarize  # noqa: E402


def parse_price(text):
    label, _, pair = text.partition("=")
    try:
        price_in, price_out = (float(x) for x in pair.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--price expects LABEL=IN,OUT, got {text!r}") from None
    if not label or price_in < 0 or price_out < 0:
        raise argparse.ArgumentTypeError(f"--price expects LABEL=IN,OUT, got {text!r}")
    return label, (price_in, price_out)


def load_groups(specs, exclude):
    groups = {}
    for spec in specs:
        label, sep, path = spec.partition("=")
        if not sep or not label or not path:
            raise SystemExit(f"expected LABEL=PATH, got {spec!r}")
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        rows = [r for r in data["results"] if r["case_id"] not in exclude]
        groups.setdefault(label, []).extend(rows)
    return groups


def build_rows(groups, prices, currency):
    table = []
    for label, rows in groups.items():
        price = prices.get(label)
        m = summarize(rows, *(price or (None, None)))
        per_call, per_run = m["latency_ms_per_call"], m["latency_ms_per_run"]
        cost = m.get("cost")
        table.append(
            {
                "label": label,
                "runs": len(rows),
                "passed": sum(bool(r["pass"]) for r in rows),
                "calls": m["calls_measured"],
                "lat_mean": seconds(per_call["mean"]),
                "lat_median": seconds(per_call["median"]),
                "lat_p95": seconds(per_call["p95"]),
                "lat_max": seconds(per_call["max"]),
                "run_mean": seconds(per_run["mean"]),
                "tok_in": m["prompt_tokens_per_call"] if m["calls_with_tokens"] else "-",
                "tok_out": m["completion_tokens_per_call"] if m["calls_with_tokens"] else "-",
                "cost_call": money(cost["per_call"], currency) if cost else "-",
                "cost_run": money(cost["per_run"], currency) if cost else "-",
                "cost_total": money(cost["total"], currency) if cost else "-",
                "measured": m["calls_measured"] > 0,
            }
        )
    return table


COLUMNS = [
    ("label", "모델"),
    ("runs", "실행"),
    ("passed", "통과"),
    ("calls", "측정 호출"),
    ("lat_mean", "응답 평균"),
    ("lat_median", "중앙값"),
    ("lat_p95", "p95"),
    ("lat_max", "최대"),
    ("run_mean", "질문당 평균"),
    ("tok_in", "입력토큰/호출"),
    ("tok_out", "출력토큰/호출"),
    ("cost_call", "비용/호출"),
    ("cost_run", "비용/질문"),
    ("cost_total", "비용 합계"),
]


def render(table):
    widths = [max(len(title), *(len(str(row[key])) for row in table)) for key, title in COLUMNS]
    lines = ["  ".join(title.ljust(w) for (_, title), w in zip(COLUMNS, widths, strict=True))]
    for row in table:
        lines.append(
            "  ".join(str(row[k]).ljust(w) for (k, _), w in zip(COLUMNS, widths, strict=True))
        )
    return "\n".join(lines)


def render_markdown(table):
    head = "| " + " | ".join(title for _, title in COLUMNS) + " |"
    rule = "|" + "|".join("---" for _ in COLUMNS) + "|"
    body = ["| " + " | ".join(str(row[k]) for k, _ in COLUMNS) + " |" for row in table]
    return "\n".join([head, rule, *body]) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("files", nargs="+", metavar="LABEL=PATH")
    p.add_argument("--price", action="append", default=[], type=parse_price, metavar="LABEL=IN,OUT")
    p.add_argument("--currency", default="")
    p.add_argument("--exclude", nargs="*", default=[], metavar="CASE")
    p.add_argument("--markdown", type=Path, default=None)
    args = p.parse_args(argv)

    groups = load_groups(args.files, set(args.exclude))
    table = build_rows(groups, dict(args.price), args.currency)
    print(render(table))
    for row in table:
        if not row["measured"]:
            print(
                f"note: {row['label']} has no latency/token numbers "
                "(result file predates the recording; re-run it)"
            )
    unpriced = [row["label"] for row in table if row["cost_call"] == "-" and row["measured"]]
    if unpriced:
        print("note: no --price for " + ", ".join(unpriced) + " (cost columns left empty)")
    if args.markdown:
        args.markdown.write_text(render_markdown(table), encoding="utf-8")
        print("Wrote", args.markdown)
    return table


if __name__ == "__main__":
    main()
