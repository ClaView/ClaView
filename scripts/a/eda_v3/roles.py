"""Four-layer axis role rules of the user's draft (data/a/axis-roles-20261016/지표축_나누기_초안_20261016.md).

Layer 1: axis name -> role (read from the draft CSV).
Layer 2: generic axes (계정항목별 등) -> target if half or more of the non-total values are in the target dictionary.
Layer 3: values of indicator axes -> basis / total header / combined / target (exact or sibling majority) / measure.
Layer 4: pressed axes -> a bundle of same-named children repeated under two or more differently named parents.

Value comparison uses the base key (cleaned name, parentheses removed: "비금융법인(사용)" -> 비금융법인).
Standard library only, no network.
"""

from __future__ import annotations

import collections
import re

from common import DRAFT, axes_of, bkey, clean_value, key, read_csv, strip_parens

LAYER1_ROLE = {"대상": "target", "기준": "basis", "지표": "measure", "축마다 판정": "generic", "확인": "human"}

# Totals: excluded from the target dictionary and from the per-axis ratio denominators.
_TOTAL = re.compile(r"^(?:합계|총계|소계|계|전체|총액|합|.+총계|.+합계)$")
# Basis words: a value made only of these is a basis value (layer 3). Fixed language list (draft 2절 examples and
# common KOSIS basis words), not tuned to cases. Values of the layer-1 basis axes are added automatically.
BASIS_WORDS = (
    "신규취급액", "잔액", "기준", "원화", "외화", "달러", "미달러", "엔화", "유로화", "위안화", "계약통화",
    "명목", "실질", "원계열", "계절조정", "말잔", "평잔", "금액", "비중", "구성비", "자산", "부채",
    "운용", "원천", "시가", "장부가", "단기", "장기", "표시",
)
_BASIS_EXTRA = re.compile(r"\d{4}년")  # "2024년기준"
# "A-B" with Hangul on at least one side ("Non-MMF부문" is one name), or a (사용)/(원천) tail
_COMBINED = re.compile(r"[가-힣)]\s*-\s*[가-힣A-Za-z(]|[가-힣A-Za-z)]\s*-\s*[가-힣(]|\((?:사용|원천)\)\s*$")
_AGE = re.compile(r"^\d{2}\s*(?:대|세)")

ROLE_KO = {"target": "대상", "target_sibling": "대상(형제)", "basis": "기준", "header": "합계 머리",
           "combined": "결합", "measure": "지표", "human": "사람 확인", "generic": "축마다 판정"}


def base(name: str | None) -> str:
    """Comparison key of a value: cleaned name with parentheses removed. A name that is only a parenthesis
    ("(서울)" used as a sub-heading) has an empty key and is left out of dictionary ratios."""
    return bkey(name)


def is_total_key(k: str) -> bool:
    return bool(_TOTAL.match(k))


def basis_only(k: str, vocab: set[str]) -> bool:
    if not k:
        return False
    rest = k
    for w in sorted(vocab | {key(w) for w in BASIS_WORDS}, key=len, reverse=True):
        if w:
            rest = rest.replace(w, "")
    return _BASIS_EXTRA.sub("", rest) == ""


def is_combined(name: str) -> bool:
    return bool(_COMBINED.search(clean_value(name)))


def layer1_table() -> dict[str, str]:
    """Axis name -> layer-1 role, read from the user's draft CSV (the rule input of layer 1)."""
    return {r["축 이름"]: LAYER1_ROLE[r["역할 초안"]] for r in read_csv(DRAFT / "축이름_역할_초안.csv")}


class AxisRoles:
    def __init__(self, meta: dict):
        self.meta = meta
        self.l1 = layer1_table()
        self.axes = []
        for tid, t in meta.items():
            for (obj_id, name), vals in axes_of(t).items():
                if name is None:
                    continue  # unnamed axis (one table) is outside the 29 names
                self.axes.append({"tid": tid, "obj_id": obj_id, "name": name, "values": vals})
        self._build_target_dict()
        self._build_basis_vocab()
        self._judge_generic()
        self._classify_values()
        self._pressed()

    # ---- layer 2 dictionary
    def _build_target_dict(self):
        src = collections.defaultdict(set)
        dropped = collections.Counter()
        for ax in self.axes:
            if self.l1.get(ax["name"]) != "target":
                continue
            for v in ax["values"]:
                k = base(v.get("ITM_NM"))
                if not k:
                    continue
                if is_total_key(k):
                    dropped["합계"] += 1
                elif k.startswith("기타"):
                    dropped["기타…"] += 1
                elif k.endswith("대출금") or k.endswith("자금"):
                    dropped["…대출금/…자금"] += 1
                else:
                    src[k].add(ax["name"])
        self.age_added = set()
        for ax in self.axes:
            for v in ax["values"]:
                if _AGE.match(clean_value(v.get("ITM_NM"))):
                    k = base(v.get("ITM_NM"))
                    if k not in src:
                        self.age_added.add(k)
                    src[k].add("연령대(추가)")
        self.target_dict = dict(src)
        self.target_dropped = dropped

    def _build_basis_vocab(self):
        self.basis_vocab = {
            k for ax in self.axes if self.l1.get(ax["name"]) == "basis"
            for v in ax["values"] if (k := base(v.get("ITM_NM"))) and not is_total_key(k)
        }

    def is_target_hit(self, name: str) -> bool:
        return base(name) in self.target_dict

    def target_ratio(self, vals: list[dict]) -> tuple[float, int, int]:
        names = [v.get("ITM_NM") or "" for v in vals]
        names = [n for n in names if base(n) and not is_total_key(base(n))]
        hit = sum(1 for n in names if self.is_target_hit(n))
        return (hit / len(names) if names else 0.0), hit, len(names)

    def _judge_generic(self):
        for ax in self.axes:
            role = self.l1.get(ax["name"], "unknown")
            ax["layer1"] = role
            ax["ratio"], ax["hit"], ax["n_nontotal"] = self.target_ratio(ax["values"])
            ax["role"] = ("target" if ax["ratio"] >= 0.5 else "measure") if role == "generic" else role

    # ---- layer 3
    def _classify_values(self):
        self.value_role = {}  # (tid, obj_id, itm_id) -> role
        for ax in self.axes:
            vals = ax["values"]
            by_parent = collections.defaultdict(list)
            has_child = set()
            for v in vals:
                by_parent[v.get("UP_ITM_ID")].append(v)
                if v.get("UP_ITM_ID"):
                    has_child.add(v["UP_ITM_ID"])
            if ax["role"] != "measure":
                # target axes: the total row ("총고정자본형성" atop an industry axis) is a total header, as in draft 4절;
                # it does not change the layer-3 counts, which cover indicator axes only
                for v in vals:
                    k = base(v.get("ITM_NM"))
                    head = ax["role"] == "target" and (
                        is_total_key(k) or (v["ITM_ID"] in has_child and (k.startswith("총") or k.startswith("합계"))))
                    self.value_role[(ax["tid"], ax["obj_id"], v["ITM_ID"])] = "header" if head else ax["role"]
                continue
            for v in vals:
                nm = v.get("ITM_NM") or ""
                k = base(nm)
                if basis_only(k, self.basis_vocab):
                    r = "basis"
                elif is_total_key(k) or (v["ITM_ID"] in has_child and (k.startswith("총") or k.startswith("합계"))):
                    r = "header"
                elif is_combined(nm):
                    r = "combined"
                elif k in self.target_dict:
                    r = "target"
                else:
                    ratio, _hit, n = self.target_ratio(by_parent[v.get("UP_ITM_ID")])
                    r = "target_sibling" if n >= 2 and ratio >= 0.5 else "measure"
                self.value_role[(ax["tid"], ax["obj_id"], v["ITM_ID"])] = r
            ax["value_roles"] = collections.Counter(self.value_role[(ax["tid"], ax["obj_id"], v["ITM_ID"])] for v in vals)

    # ---- layer 4
    def _pressed(self):
        self.pressed = []
        for ax in self.axes:
            if ax["role"] != "measure":
                continue
            names = {v["ITM_ID"]: base(v.get("ITM_NM")) for v in ax["values"]}
            disp = {v["ITM_ID"]: strip_parens(clean_value(v.get("ITM_NM"))) or clean_value(v.get("ITM_NM"))
                    for v in ax["values"]}
            kids = collections.defaultdict(set)
            for v in ax["values"]:
                p = v.get("UP_ITM_ID")
                if p and p in names and names[v["ITM_ID"]]:
                    kids[p].add(names[v["ITM_ID"]])
            parents_of = collections.defaultdict(set)
            for p, ks in kids.items():
                for k in ks:
                    parents_of[k].add(names[p])
            repeated = {k for k, ps in parents_of.items() if len(ps) >= 2}
            if len(repeated) < 2:
                continue
            plist = [p for p in kids if len(kids[p] & repeated) >= 2]
            pairs = [(a, b) for i, a in enumerate(plist) for b in plist[i + 1:]
                     if names[a] != names[b] and len(kids[a] & kids[b] & repeated) >= 2]
            if not pairs:
                continue
            bundle = set().union(*(kids[a] & kids[b] & repeated for a, b in pairs))
            parents = {names[p] for ab in pairs for p in ab}
            pdisp = sorted({disp[p] for ab in pairs for p in ab})
            kdisp = sorted({disp[i] for i, k in names.items() if k in bundle})
            up_t = sum(1 for k in parents if k in self.target_dict) / len(parents)
            lo_t = sum(1 for k in bundle if k in self.target_dict) / len(bundle)
            up_b = sum(1 for k in parents if basis_only(k, self.basis_vocab)) / len(parents)
            lo_b = sum(1 for k in bundle if basis_only(k, self.basis_vocab)) / len(bundle)

            def lvl(t, b):
                return "target" if t >= 0.5 else ("basis" if b >= 0.5 else "measure")

            self.pressed.append({
                "tid": ax["tid"], "name": ax["name"], "obj_id": ax["obj_id"],
                "n_repeated": len(bundle), "repeated_disp": kdisp, "parents_disp": pdisp,
                "upper_target_ratio": round(up_t, 2), "lower_target_ratio": round(lo_t, 2),
                "upper_role": lvl(up_t, up_b), "lower_role": lvl(lo_t, lo_b),
            })

    # ---- axis-name-only baseline (for comparison)
    def name_only_role(self, axis_name: str) -> str:
        r = self.l1.get(axis_name, "unknown")
        return "measure" if r == "generic" else r
