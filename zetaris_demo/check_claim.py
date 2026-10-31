"""Check a GBP figure in an agent's claim against Zetaris, with Vidhur's deterministic Evaluator number check.

    python zetaris_demo/check_claim.py "Korean HBM part P002 exposure is GBP 3,300,000 this quarter"
    python zetaris_demo/check_claim.py            # runs the two demo claims: a 10x-wrong one, then a correct one

The real figure is the latest-quarter GBP exposure of the part at today's live ECB rate (ganga.purchases x ganga_live.fx_live).
The verdict comes from agents.evaluator.gbp_findings, the same check the product uses (10% tolerance). No model, no keys printed.
"""

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

from agents.evaluator import gbp_findings  # noqa: E402
from core.claims import Claim  # noqa: E402

DEMO_CLAIMS = [
    "Korean HBM part P002 exposure is GBP 3,300,000 this quarter",
    "Korean HBM part P002 exposure is GBP 330,000 this quarter",
]

SQL = """SELECT p.part_id, s.country AS supplier_country, p.currency, f.rate_per_gbp, f.rate_date,
       ROUND(SUM(p.quantity * p.unit_price) / f.rate_per_gbp, 2) AS gbp_exposure_live
FROM ganga.purchases p
JOIN ganga.parts pt ON p.part_id = pt.part_id
JOIN ganga.suppliers s ON pt.supplier_id = s.supplier_id
JOIN ganga_live.fx_live f ON p.currency = f.currency
WHERE p.quarter = (SELECT MAX(quarter) FROM ganga.purchases) AND p.part_id = '{part_id}'
GROUP BY p.part_id, s.country, p.currency, f.rate_per_gbp, f.rate_date"""

RED, GREEN, CYAN, BOLD, RESET = "\033[91m", "\033[92m", "\033[96m", "\033[1m", "\033[0m"


def part_in(text):
    match = re.search(r"\bP\d{3}\b", text)
    if not match:
        raise ValueError("The claim must name a part id such as P002.")
    return match.group(0)


def verdict(text, real_gbp, part_id):
    """(passed, reasons) from the Evaluator's GBP check against the real Zetaris figure."""
    claim = Claim("CHECK", text, part_id, "", "", "cost_up", 0.0, 0.0)
    findings = gbp_findings(claim, {"baseline_gbp": real_gbp, "result_gbp": {}})
    return not findings, [f"{f.code}: {f.message}" for f in findings]


def check(text, client, cache=None):
    part_id = part_in(text)
    sql = SQL.format(part_id=part_id)
    cache = {} if cache is None else cache
    if part_id not in cache:  # one Zetaris query per part, even when several claims name it
        cache[part_id] = client.query(sql, agent="check_claim")
    rows = cache[part_id]
    if not rows:
        raise ValueError(f"Zetaris has no latest-quarter purchases for {part_id}.")
    row = rows[0]
    passed, reasons = verdict(text, float(row["gbp_exposure_live"]), part_id)
    return passed, reasons, row, sql


def show(text, passed, reasons, row, sql):
    print(f"\n{BOLD}CLAIM:{RESET} {text}")
    if passed:
        print(f"\n{GREEN}{BOLD}  ##  PASS  ##{RESET}")
    else:
        print(f"\n{RED}{BOLD}  ##  SENT BACK  ##{RESET}")
        for reason in reasons:
            print(f"{RED}  Reason: {reason}{RESET}")
    print(f"\n{CYAN}{BOLD}  Real Zetaris figure: GBP {float(row['gbp_exposure_live']):,.0f}{RESET}"
          f"  ({row['part_id']}, {row['supplier_country']}, {row['currency']} at {row['rate_per_gbp']} per GBP, ECB {row['rate_date']})")
    print(f"\n{BOLD}  SQL used:{RESET}\n" + "\n".join("    " + line for line in sql.splitlines()))
    print("-" * 78)


def main(argv):
    os.system("")  # turns on ANSI colours in the Windows console
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    from core.zetaris_client import ZetarisClient

    client = ZetarisClient(timeout=30)
    started, cache = time.monotonic(), {}
    for text in (argv or DEMO_CLAIMS):
        show(text, *check(text, client, cache))
    print(f"Done in {time.monotonic() - started:.1f}s")


if __name__ == "__main__":
    main(sys.argv[1:])
