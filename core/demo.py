"""Demo mode (`python main.py --demo`): one company run limited to the two biggest-spend drivers, on a dated cached evidence set.

The fixed plan is chosen by rule, not by the Planner model, to keep the run short:
  D1  geopolitics on the largest-spend part (the Taiwan GPU chip)
  D2  currency on the largest-spend part whose supplier currency has ECB data (the currency driver; FX stays live)
The weak-claim and Red Team demo instructions are both on, so every demo run shows a send-back and a challenge.
"""

from datetime import date

from agents.currency_analyst import CurrencyAnalyst
from agents.geopolitics_analyst import GeopoliticsAnalyst
from core import fx

TIME_BUDGET_SECONDS = 1500  # 25 min: no model call starts after this; unfinished drivers are labelled "Could not verify"
CALL_TIMEOUT_SECONDS = 180
ARTICLES_SHOWN = 5  # fewer articles per prompt: shorter prompts answer faster on the shared runtime
CALL_MAX_ATTEMPTS = 3
RETRY_WAIT_SECONDS = 2
WORKERS = 2


def pick_targets(targets, supported_currencies=None):
    """(geopolitics_target, currency_target) from spend-ordered targets. currency_target is None if no part has FX data."""
    geo = targets[0]
    if supported_currencies is None:
        supported_currencies = fx.supported_currencies()
    cur = next((t for t in targets if fx.COUNTRY_CURRENCY.get(t.country) in supported_currencies and t.part_id != geo.part_id), None)
    return geo, cur


def demo_plan(targets, supported_currencies=None):
    geo, cur = pick_targets(targets, supported_currencies)
    total = sum(t.quarterly_spend_gbp for t in targets)
    delegations = [{"analyst": "geopolitics", "part_id": geo.part_id, "work_id": "D1",
                    "reason": f"Demo plan (fixed rule): the largest-spend part, GBP {geo.quarterly_spend_gbp:,.0f} ({geo.quarterly_spend_gbp / total:.0%} of spend) from {geo.country}."}]
    if cur:
        delegations.append({"analyst": "currency", "part_id": cur.part_id, "work_id": "D2",
                            "reason": f"Demo plan (fixed rule): the largest-spend part whose supplier currency ({fx.COUNTRY_CURRENCY[cur.country]}) has ECB data, "
                                      f"GBP {cur.quarterly_spend_gbp:,.0f} ({cur.quarterly_spend_gbp / total:.0%} of spend) from {cur.country}."})
    chosen = {d["part_id"] for d in delegations}
    skipped = [{"analyst": "any", "part_id": t.part_id, "reason": "Outside the two-driver demo scope (run without --demo for the full plan)."}
               for t in targets if t.part_id not in chosen]
    return {"summary": "DEMO MODE: the Planner model was not called. Two biggest-spend drivers chosen by a fixed rule so the run finishes in minutes.",
            "delegations": delegations, "skipped": skipped, "fallback": False, "demo": True}


def evidence_queries(targets, supported_currencies=None):
    """Every news query the demo run can make (normal and broadened, for both analysts) - what scripts/save_demo_evidence.py caches."""
    geo, cur = pick_targets(targets, supported_currencies)
    queries = [(GeopoliticsAnalyst._query(geo, False), True), (GeopoliticsAnalyst._query(geo, True), True)]
    if cur:
        currency = fx.COUNTRY_CURRENCY[cur.country]
        queries += [(CurrencyAnalyst._query(cur, currency, False), False), (CurrencyAnalyst._query(cur, currency, True), False)]
    return queries  # (query, publisher_feeds)


def today():
    return date.today().isoformat()
