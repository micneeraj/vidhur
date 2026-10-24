"""Build the CFO dashboard from saved run files only (no model call, no service).

Usage:  python scripts/build_dashboard.py      (same as: python main.py --dashboard)
Writes: output_examples/dashboard.html  (one self-contained file; open it by double-click, works offline)
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.chdir(Path(__file__).resolve().parents[1])

from core import dashboard  # noqa: E402


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    runs = dashboard.default_runs()
    out = dashboard.write_dashboard(runs)
    print(f"Dashboard saved: {out} ({len(runs)} run(s): {', '.join(r['label'] for r in runs)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
