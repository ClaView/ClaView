"""Create a separate source-checked draft experiment without editing runtime mappings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from claview.b.a1_mappings import write_review
from claview.b.metadata import MetadataStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog/search_catalog.json"))
    parser.add_argument("--metadata", type=Path, default=Path("data/raw/boK_meta_raw.jsonl"))
    parser.add_argument(
        "--mappings", type=Path, default=Path("data/mappings/runtime/search_mappings.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    review = write_review(MetadataStore(args.catalog, args.metadata), args.mappings, args.output)
    print(
        json.dumps(
            {
                "reviewed": len(review["reviews"]),
                "experiment_eligible": len(review["selected_mapping_ids"]),
                "operational_approved": 0,
                "human_review": False,
                "external_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
