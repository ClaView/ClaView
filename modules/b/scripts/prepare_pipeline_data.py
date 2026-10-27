"""Import saved public metadata and gold labels; never download numeric values."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    transfers = {
        "data/search/catalog-349.json": "data/catalog/search_catalog.json",
        "data/metadata/boK_meta_raw.jsonl": "data/raw/boK_meta_raw.jsonl",
        "current/golden_set_v1.json": "eval/cases/golden/golden_set_v1.json",
        "current/golden_set_v1.schema.json": "eval/cases/golden/golden_set_v1.schema.json",
    }
    records = []
    for source, destination in transfers.items():
        src, dest = args.source_root / source, root / destination
        content = src.read_bytes()
        # Refuse overwriting an independently changed artifact.
        if dest.exists() and dest.read_bytes() != content:
            raise SystemExit(f"Different destination already exists: {destination}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        records.append(
            {
                "source_name": src.name,
                "destination": destination,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    (root / "data/catalog/pipeline_manifest.json").write_text(
        json.dumps(
            {
                "import_date": "2026-10-01",
                "scope": "saved metadata; no numeric verification",
                "files": records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("Imported catalog, saved metadata, and unchanged golden labels.")


if __name__ == "__main__":
    main()
