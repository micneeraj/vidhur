"""Demo mode (`python main.py --demo`): one company run limited to three drivers, on a dated cached evidence set.

The fixed plan is chosen by rule, not by the Planner model, to keep the run short:
  D1  geopolitics on the largest-spend part (the Taiwan GPU chip)
  D2  currency on the largest-spend part whose supplier currency has ECB data (the currency driver; FX stays live)
  D3  supplier_commodity on the largest-spend memory part (the South Korea HBM stack); omitted if Ganga buys no memory part
The weak-claim and Red Team demo instructions are both on, so every demo run shows a send-back and a challenge.
"""

from datetime import date

from agents.currency_analyst import CurrencyAnalyst
from agents.geopolitics_analyst import GeopoliticsAnalyst
from agents.supplier_commodity import SupplierCommodityAnalyst
from core import fx

TIME_BUDGET_SECONDS = 1500  # 25 min: no model call starts after this; unfinished drivers are labelled "Could not verify"
CALL_TIMEOUT_SECONDS = 180
ARTICLES_SHOWN = 5  # fewer articles per prompt: shorter prompts answer faster on the shared runtime
CALL_MAX_ATTEMPTS = 3
RETRY_WAIT_SECONDS = 2
WORKERS = 3  # one per driver (D1, D2, D3), so the third driver does not queue behind the first two


def pick_targets(targets, supported_currencies=None):
    """(geopolitics_target, currency_target) from spend-ordered targets. currency_target is None if no part has FX data."""
    geo = targets[0]
    if supported_currencies is None:
        supported_currencies = fx.supported_currencies()
    cur = next((t for t in targets if t.fx_currency in supported_currencies and t.part_id != geo.part_id), None)
    return geo, cur


def pick_memory_target(targets, taken=()):
    """The largest-spend memory part (category mentions memory) not already used by D1/D2, or None."""
    return next((t for t in targets if "memory" in t.category.lower() and t.part_id not in taken), None)


DRIVER_IDS = ("D1", "D2", "D3")


def parse_drivers(text):
    """'D1,D3' -> ['D1', 'D3'] (deduplicated, in D1..D3 order). Raises ValueError on an empty or unknown id."""
    ids = [p.strip().upper() for p in str(text).split(",")]
    bad = [p for p in ids if p not in DRIVER_IDS]
    if bad or not all(ids):
        raise ValueError(f"unknown driver(s) {', '.join(repr(b) for b in bad) or '(empty)'}: use D1, D2, D3 or a comma list such as D1,D3")
    return [d for d in DRIVER_IDS if d in ids]


def demo_plan(targets, supported_currencies=None, drivers=None):
    """Fixed demo plan. With `drivers` (e.g. ['D3']) only those are delegated; the rest go to plan['not_run'] (same parts as a full demo run)."""
    geo, cur = pick_targets(targets, supported_currencies)
    mem = pick_memory_target(targets, {geo.part_id, cur.part_id if cur else None})
    total = sum(t.quarterly_spend_gbp for t in targets)
    delegations = [{"analyst": "geopolitics", "part_id": geo.part_id, "work_id": "D1",
                    "reason": f"Demo plan (fixed rule): the largest-spend part, GBP {geo.quarterly_spend_gbp:,.0f} ({geo.quarterly_spend_gbp / total:.0%} of spend) from {geo.country}."}]
    if cur:
        delegations.append({"analyst": "currency", "part_id": cur.part_id, "work_id": "D2",
                            "reason": f"Demo plan (fixed rule): the largest-spend part whose supplier currency ({cur.fx_currency}) has ECB data, "
                                      f"GBP {cur.quarterly_spend_gbp:,.0f} ({cur.quarterly_spend_gbp / total:.0%} of spend) from {cur.country}."})
    if mem:
        delegations.append({"analyst": "supplier_commodity", "part_id": mem.part_id, "work_id": "D3",
                            "reason": f"Demo plan (fixed rule): the largest-spend memory part, GBP {mem.quarterly_spend_gbp:,.0f} "
                                      f"({mem.quarterly_spend_gbp / total:.0%} of spend) from {mem.country}; memory contract prices and "
                                      "supplier concentration are the risk neither the geopolitics nor the currency driver covers."})
    chosen = {d["part_id"] for d in delegations}
    skipped = [{"analyst": "any", "part_id": t.part_id, "reason": "Outside the three-driver demo scope (run without --demo for the full plan)."}
               for t in targets if t.part_id not in chosen]
    plan = {"summary": f"DEMO MODE: the Planner model was not called. {len(delegations)} drivers chosen by a fixed rule so the run finishes in minutes.",
            "delegations": delegations, "skipped": skipped, "fallback": False, "demo": True}
    if drivers:
        run = [d for d in delegations if d["work_id"] in drivers]
        plan["not_run"] = [d for d in delegations if d["work_id"] not in drivers]
        plan["delegations"] = run
        plan["summary"] = (f"DEMO MODE --drivers {','.join(d['work_id'] for d in run)}: the Planner model was not called. "
                           f"{len(run)} of {len(delegations)} fixed-rule drivers run; the rest are not started.")
    return plan


def evidence_queries(targets, supported_currencies=None):
    """Every news query the demo run can make (normal and broadened, for all analysts) - what scripts/save_demo_evidence.py caches."""
    geo, cur = pick_targets(targets, supported_currencies)
    mem = pick_memory_target(targets, {geo.part_id, cur.part_id if cur else None})
    queries = [(GeopoliticsAnalyst._query(geo, False), True), (GeopoliticsAnalyst._query(geo, True), True)]
    if mem:
        queries += [(SupplierCommodityAnalyst._query(mem, False), True), (SupplierCommodityAnalyst._query(mem, True), True)]
    if cur:
        currency = cur.fx_currency
        queries += [(CurrencyAnalyst._query(cur, currency, False), False), (CurrencyAnalyst._query(cur, currency, True), False)]
    return queries  # (query, publisher_feeds)


def today():
    return date.today().isoformat()
