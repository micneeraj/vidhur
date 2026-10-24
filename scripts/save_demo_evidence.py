"""Save the dated evidence set that `python main.py --demo` replays.

Usage: python scripts/save_demo_evidence.py [--missing]
--missing: fetch only the queries that have no saved file yet and merge them into manifest.json (the saved ones are left untouched).
Fetches today's real news LIVE for the demo's three drivers (the queries the analysts make, normal and broadened) and stores the raw
articles in data/demo_evidence/ with manifest.json recording the date. Re-run it to refresh the demo evidence.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.chdir(Path(__file__).resolve().parents[1])
sys.stdout.reconfigure(encoding="utf-8")

from core import demo, news  # noqa: E402
from core.ganga_data import top_targets  # noqa: E402
from core.ganga_data import Target  # noqa: E402
from core.zetaris_client import ZetarisClient, ZetarisUnreachable  # noqa: E402


def targets_from_csv():
    """Same targets as top_targets(), read from the CSVs that scripts/load_ganga_to_zetaris.py loads (used only if Zetaris is down)."""
    import csv
    read = lambda name: list(csv.DictReader(open(Path("data") / name, encoding="utf-8")))  # noqa: E731
    parts, sups = {r["part_id"]: r for r in read("parts.csv")}, {r["supplier_id"]: r for r in read("suppliers.csv")}
    rows = read("purchases.csv")
    quarter = max(r["quarter"] for r in rows)
    rows = sorted((r for r in rows if r["quarter"] == quarter), key=lambda r: -float(r["total_gbp"]))
    return [Target(r["part_id"], parts[r["part_id"]]["part_name"], parts[r["part_id"]]["category"],
                   sups[parts[r["part_id"]]["supplier_id"]]["country"], sups[parts[r["part_id"]]["supplier_id"]]["supplier_name"],
                   quarter, float(r["total_gbp"]), r["currency"]) for r in rows]


def main():
    only_missing = "--missing" in sys.argv[1:]
    try:
        targets = top_targets(ZetarisClient(), 99)
    except ZetarisUnreachable:
        print("Zetaris is not running; reading the same parts/suppliers/purchases CSVs it is loaded from.")
        targets = targets_from_csv()
    manifest_path = news.DEMO_DIR / "manifest.json"
    if only_missing and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"cached_on": demo.today(), "note": "Raw articles fetched live on this date for `main.py --demo`.", "queries": []}
    done = {q["query"] for q in manifest["queries"]} if only_missing else set()
    for query, feeds in demo.evidence_queries(targets):
        if query in done and (news.DEMO_DIR / news._cache_path(query).name).exists():
            print(f"      kept   <- {query}")
            continue
        n = news.save_demo_evidence(query, publisher_feeds=feeds)
        print(f"{n:3d} articles <- {query}")
        manifest["queries"].append({"query": query, "articles": n, "file": news._cache_path(query).name})
    news.DEMO_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    if any(q["articles"] == 0 for q in manifest["queries"]):
        print("WARNING: a query returned no articles; that driver would be 'Could not verify' in the demo.")
        return 1
    print(f"Saved to {news.DEMO_DIR} (cached_on {manifest['cached_on']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
