"""Plan 19 stages 1-2 entry point (offline; no model or network call; the stored batch is only read).

    python scripts/plan19_baseline.py baseline --store <plan18 store> --out runs/plan19/baseline
    python scripts/plan19_baseline.py reverify --store <plan18 store> --baseline runs/plan19/baseline --out runs/plan19/reverify_current
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claview_v3.development_reverify import main  # noqa: E402

if __name__ == "__main__":
    main()
