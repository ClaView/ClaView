"""Data locations for claview_a, in one place.

Paths only: nothing here opens or reads a file. When the package moves, change ROOT (and the outside entry points)
and nothing else. ROOT is the checkout that holds ``modules/a/claview_a``.
"""

from __future__ import annotations
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]

# Catalog raw data (349 tables): table list and metadata snapshot.
CATALOG_DATA_DIR = ROOT / "data"
CATALOG_SEARCH = CATALOG_DATA_DIR / "catalog" / "search_catalog.json"
CATALOG_META_RAW = CATALOG_DATA_DIR / "raw" / "boK_meta_raw.jsonl"


# Evaluation cases in this checkout: reduced sets and case roles.
CASES_DIR = ROOT / "eval" / "cases" / "a"


# External synonym draft (outside git). Read when present, skipped when absent.
EXTERNAL_SYNONYMS = ROOT / "runs" / "eda_v3" / "6_external_synonyms" / "synonyms_external.jsonl"

# Axis-role inputs supplied by the user (draft note and three CSV files, kept as given).
AXIS_ROLES_INPUT_DIR = ROOT / "data" / "a" / "axis-roles-20261016"


AXIS_NAME_ROLES = AXIS_ROLES_INPUT_DIR / "축이름_역할_초안.csv"
GENERIC_AXIS_ROLES = AXIS_ROLES_INPUT_DIR / "일반축_축마다_판정.csv"
COLLAPSED_AXES = AXIS_ROLES_INPUT_DIR / "눌린축_목록.csv"

# Package data files (checked in with the package; catalog annotation data, no question or answer content).
PACKAGE_DIR = Path(__file__).resolve().parent
PACKAGE_DATA_DIR = PACKAGE_DIR / "data"
AXIS_ROLE_OVERRIDES = PACKAGE_DATA_DIR / "axis_role_overrides.csv"
BASIS_WORDS = PACKAGE_DATA_DIR / "basis_words.txt"
SYNONYMS_APPROVED = PACKAGE_DATA_DIR / "synonyms_approved.jsonl"
OUT_OF_SCOPE = PACKAGE_DATA_DIR / "out_of_scope.jsonl"
CONDITION_WORDS = PACKAGE_DATA_DIR / "condition_words.json"  # word lists of find_conditions (2.2)


LEXICON = PACKAGE_DATA_DIR / "lexicon.json"  # grammar words of question interpretation (plan 08 T2)
EVIDENCE_WORDS = PACKAGE_DATA_DIR / "evidence_words.json"  # word lists and patterns of table selection (3.1-3.5)


SELECTION_POLICY = PACKAGE_DATA_DIR / "selection_policy.json"  # unset dimensions: representative / keep_all


ROLE_WORDS = PACKAGE_DATA_DIR / "role_words.json"  # word lists of the axis-role table (plan 11 R1b)


# EDA v3 outputs (outside git): source of the draft overrides and the numbers shown next to the catalog stats.
EDA_DIR = ROOT / "runs" / "eda_v3"
CONFIRM_SHEET_RECOMMENDED = EDA_DIR / "2_axis_roles" / "confirm_sheet_recommended.csv"


# Outputs of stretch 1 (outside git).
V3_RUNS_DIR = ROOT / "runs" / "v3_stretch1"


# Decisions of the overrides file that found no row when the file was regenerated (import_overrides).
ORPHAN_DECISIONS = V3_RUNS_DIR / "orphan_decisions.csv"
