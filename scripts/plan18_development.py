"""Entry point for the explicitly approved development batch; no calls on import."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from claview.a.development import main

if __name__ == "__main__":
    main()
