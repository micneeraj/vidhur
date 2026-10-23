"""Is everything Vidhur needs up? Prints a PASS/FAIL list (under 30 s). Exit code 0 only if all checks pass.

Usage:  python scripts/healthcheck.py
Checks: required env vars, Nightshift reachable + token valid (one tiny call, shows the answering model),
        Zetaris reachable + the ganga tables present.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.chdir(Path(__file__).resolve().parents[1])

from core.preflight import run_checks  # noqa: E402


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    checks = run_checks()
    for c in checks:
        print(c.line())
    ok = all(c.ok for c in checks)
    print("\nHEALTHCHECK " + ("PASS - ready to run: python main.py" if ok else "FAIL - fix the FAIL lines above first"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
