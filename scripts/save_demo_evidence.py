"""Save the dated evidence set that `python main.py --demo` replays.

Usage: python scripts/save_demo_evidence.py
Fetches today's real news LIVE for the demo's two drivers (the queries the analysts make, normal and broadened) and stores the raw
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
from core.zetaris_client import ZetarisClient  # noqa: E402


def main():
    targets = top_targets(ZetarisClient(), 99)
    manifest = {"cached_on": demo.today(), "note": "Raw articles fetched live on this date for `main.py --demo`.", "queries": []}
    for query, feeds in demo.evidence_queries(targets):
        n = news.save_demo_evidence(query, publisher_feeds=feeds)
        print(f"{n:3d} articles <- {query}")
        manifest["queries"].append({"query": query, "articles": n, "file": news._cache_path(query).name})
    news.DEMO_DIR.mkdir(parents=True, exist_ok=True)
    (news.DEMO_DIR / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    if any(q["articles"] == 0 for q in manifest["queries"]):
        print("WARNING: a query returned no articles; that driver would be 'Could not verify' in the demo.")
        return 1
    print(f"Saved to {news.DEMO_DIR} (cached_on {manifest['cached_on']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
