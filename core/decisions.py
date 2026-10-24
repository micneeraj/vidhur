"""Decision options and their GBP cost. ALL maths is plain Python - the model only picks an option and explains it.

For one verified claim (baseline quarterly spend S, impact worst / likely / best), an option protects a share h of the
spend and pays a premium p% on that share. The cost of an option in a scenario is the CHANGE in quarterly cost versus the
baseline (+ is extra cost, - is a saving):

    cost = (1 - h) * scenario_change + h * S * p / 100

Scenarios: worst / likely / best (the claim's own range) and "forecast_wrong", the mirror image of the likely case
(the market moves the opposite way by the likely amount). The premiums are ASSUMPTIONS, stated in the CFO brief.
"""

import csv

from core.config import ROOT

KINDS = ("NO_ACTION", "HEDGE", "LOCK_PRICE", "DUAL_SOURCE")
LABELS = {
    "NO_ACTION": "No action, monitor",
    "HEDGE": "Hedge a share of next quarter's spend",
    "LOCK_PRICE": "Lock the price with the supplier (100% of volume)",
    "DUAL_SOURCE": "Move a share of volume to a second supplier",
}
ASSUMPTIONS_FILE = ROOT / "data" / "decision_assumptions.csv"
ASSUMPTIONS_NOTE = "Assumptions: see data/decision_assumptions.csv"


def load_premiums(path=ASSUMPTIONS_FILE):
    """Assumed cost of protection, % of the protected spend, read from the CSV (illustrative; a real quote would replace them)."""
    with open(path, newline="", encoding="utf-8") as f:
        premiums = {row["option"].strip(): float(row["premium_pct"]) for row in csv.DictReader(f)}
    missing = [k for k in KINDS if k not in premiums]
    if missing:
        raise ValueError(f"{path} has no premium_pct for: {', '.join(missing)}")
    return premiums


PREMIUM_PCT = load_premiums()
# The comparison table every decision is shown against.
MENU = (("NO_ACTION", 0), ("HEDGE", 50), ("LOCK_PRICE", 100), ("DUAL_SOURCE", 30))
SCENARIOS = ("worst", "likely", "best", "forecast_wrong")
MIN_SHARE, MAX_SHARE = 5, 100


def normalize_choice(kind, share_pct):
    """Clamp a model's choice to something executable. Returns (kind, share_pct) or None if the kind is unknown."""
    kind = str(kind or "").strip().upper().replace(" ", "_").replace("-", "_")
    if kind not in KINDS:
        return None
    if kind == "NO_ACTION":
        return kind, 0
    if kind == "LOCK_PRICE":
        return kind, 100
    try:
        share = float(share_pct)
    except (TypeError, ValueError):
        return None
    return kind, int(round(min(max(share, MIN_SHARE), MAX_SHARE)))


def rule_choice(impact):
    """Deterministic rule applied BEFORE any model proposal: protection only makes sense against a cost increase. If the worst case
    of the scenario range is not a cost increase (every scenario is a saving or zero), the choice is fixed to NO_ACTION / monitor.
    Returns (kind, share_pct, rationale) when the rule fires, else None. impact: worst_gbp."""
    if impact["worst_gbp"] > 0:
        return None
    return ("NO_ACTION", 0, f"Rule: the worst case is {gbp(impact['worst_gbp'])}, not a cost increase, so there is nothing to protect "
            "against; no hedge, price lock or second supplier. Monitor.")


def scenarios(impact):
    """The four scenario changes in quarterly cost (GBP) for doing nothing. impact: worst_gbp / likely_gbp / best_gbp."""
    return {"worst": impact["worst_gbp"], "likely": impact["likely_gbp"], "best": impact["best_gbp"],
            "forecast_wrong": -impact["likely_gbp"]}


def option_cost(spend_gbp, impact, kind, share_pct):
    """Change in quarterly cost for one option under each scenario, plus the premium paid. Rounded to the pound."""
    h = share_pct / 100
    premium = spend_gbp * h * PREMIUM_PCT[kind] / 100
    cost = {name: round((1 - h) * change + premium) for name, change in scenarios(impact).items()}
    return {"kind": kind, "share_pct": share_pct, "premium_gbp": round(premium), **cost}


def compare_options(spend_gbp, impact, chosen=None):
    """Cost of the menu options (and the chosen one if it is not on the menu), each under every scenario."""
    rows = [option_cost(spend_gbp, impact, k, s) for k, s in MENU]
    if chosen and not any((r["kind"], r["share_pct"]) == tuple(chosen) for r in rows):
        rows.append(option_cost(spend_gbp, impact, *chosen))
    return rows


def concentration(targets, part_id):
    """Python facts about how concentrated Ganga's spend is around one part: its share, its country's and its supplier's."""
    total = sum(t.quarterly_spend_gbp for t in targets) or 1.0
    me = next(t for t in targets if t.part_id == part_id)
    country = sum(t.quarterly_spend_gbp for t in targets if t.country == me.country)
    supplier = sum(t.quarterly_spend_gbp for t in targets if t.supplier_name == me.supplier_name)
    return {"total_spend_gbp": round(total), "part_share_pct": round(100 * me.quarterly_spend_gbp / total, 1),
            "country": me.country, "country_share_pct": round(100 * country / total, 1),
            "supplier": me.supplier_name, "supplier_share_pct": round(100 * supplier / total, 1)}


def gbp(value):
    return f"{'+' if value > 0 else '-' if value < 0 else ''}£{abs(value):,.0f}"


def describe(kind, share_pct):
    return LABELS[kind] if kind in ("NO_ACTION", "LOCK_PRICE") else f"{LABELS[kind]}: {share_pct}%"


def options_table_text(rows):
    """Plain-text table used inside model prompts."""
    return "\n".join(
        f"{r['kind']} {r['share_pct']}% | premium {gbp(r['premium_gbp'])} | worst {gbp(r['worst'])} | likely {gbp(r['likely'])} | "
        f"best {gbp(r['best'])} | if forecast wrong {gbp(r['forecast_wrong'])}" for r in rows)
