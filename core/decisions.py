"""Decision options and their GBP cost. ALL maths is plain Python - the model only picks an option and explains it.

For one verified claim (baseline quarterly spend S, impact worst / likely / best), an option protects a share h of the
spend and pays a premium p% on that share. The cost of an option in a scenario is the CHANGE in quarterly cost versus the
baseline (+ is extra cost, - is a saving):

    cost = (1 - h) * scenario_change + h * S * p / 100

LOCK_PRICE is the exception: no price-lock premium is documented anywhere, so its cost of action is NOT QUANTIFIED (it needs a
supplier quote). It is never shown as a GBP figure (in particular never as a scenario outcome); what is shown is the risk it protects.

Scenarios: worst / likely / best (the claim's own range) and "forecast_wrong", the mirror image of the likely case
(the market moves the opposite way by the likely amount). The premiums are ASSUMPTIONS, stated in the CFO brief.
"""

import csv

from core.config import ROOT

KINDS = ("NO_ACTION", "HEDGE", "LOCK_PRICE", "DUAL_SOURCE")
UNQUOTED = ("LOCK_PRICE",)  # options whose cost needs a supplier quote: no premium is assumed, so no GBP cost is computed
NOT_QUANTIFIED = "Not quantified"
LOCK_COST_LINE = "Cost of action: Not quantified - requires a supplier quote for the price-lock premium and volume commitment."
LOCK_TRADE_OFF = "Trade-off: locking may forgo savings if prices soften."
LOCK_CHANGE_LINE = ("What would change this recommendation: a supplier quote showing the lock premium exceeds the expected avoided cost, "
                    "or evidence that supply/pricing is easing.")
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
    missing = [k for k in KINDS if k not in premiums and k not in UNQUOTED]
    if missing:
        raise ValueError(f"{path} has no premium_pct for: {', '.join(missing)}")
    return premiums


PREMIUM_PCT = load_premiums()  # HEDGE and DUAL_SOURCE are SCENARIO assumptions; there is no LOCK_PRICE premium
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
    """Deterministic rule applied BEFORE any model proposal: no hedge or price lock when the verified expected direction is cost DOWN
    (protection only makes sense against a cost increase). If the worst case
    of the scenario range is not a cost increase (every scenario is a saving or zero), the choice is fixed to NO_ACTION / monitor.
    Returns (kind, share_pct, rationale) when the rule fires, else None. impact: worst_gbp."""
    if impact["worst_gbp"] > 0:
        return None
    return ("NO_ACTION", 0, f"Rule: the worst case is {gbp(impact['worst_gbp'])}, not a cost increase, so there is nothing to protect "
            "against; no hedge or price lock (the verified expected direction is cost down). Monitor.")


def scenarios(impact):
    """The four scenario changes in quarterly cost (GBP) for doing nothing. impact: worst_gbp / likely_gbp / best_gbp."""
    return {"worst": impact["worst_gbp"], "likely": impact["likely_gbp"], "best": impact["best_gbp"],
            "forecast_wrong": -impact["likely_gbp"]}


def risk_protected_gbp(impact, share_pct):
    """The worst-case exposure (a cost increase, never negative) times the protected share: what a lock protects against."""
    return round(max(impact["worst_gbp"], 0) * share_pct / 100)


LOCK_RATIONALE_NOTE = ("Note: any GBP cost quoted for the lock in the rationale was written before lock costs were removed; it is not a "
                       "supplier quote and is not used.")


def lock_lines(impact, share_pct=100, rationale=""):
    """The four lines shown for a price lock instead of a cost: cost not quantified, risk protected (computed), trade-off, what would change.
    A saved rationale that still quotes a GBP figure for the lock gets a fifth line saying that figure is not used."""
    worst, protected = round(max(impact["worst_gbp"], 0)), risk_protected_gbp(impact, share_pct)
    risk = (f"protects against the full worst-case exposure of £{worst:,.0f}" if share_pct >= 100 else
            f"protects against {share_pct:g}% of the worst-case exposure, up to £{protected:,.0f}")
    return [LOCK_COST_LINE, f"Risk protected: {risk}.", LOCK_TRADE_OFF, LOCK_CHANGE_LINE] + ([LOCK_RATIONALE_NOTE] if "£" in rationale else [])


def option_cost(spend_gbp, impact, kind, share_pct):
    """Change in quarterly cost for one option under each scenario, plus the premium paid. Rounded to the pound.
    An option in UNQUOTED (the price lock) has premium_gbp and every scenario cost set to None, and carries risk_protected_gbp instead."""
    h = share_pct / 100
    if kind in UNQUOTED:
        return {"kind": kind, "share_pct": share_pct, "premium_gbp": None, **{name: None for name in SCENARIOS},
                "risk_protected_gbp": risk_protected_gbp(impact, share_pct), "risk_line": lock_lines(impact, share_pct)[1]}
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
    if value is None:
        return NOT_QUANTIFIED
    return f"{'+' if value > 0 else '-' if value < 0 else ''}£{abs(value):,.0f}"


def describe(kind, share_pct):
    return LABELS[kind] if kind in ("NO_ACTION", "LOCK_PRICE") else f"{LABELS[kind]}: {share_pct}%"


CHALLENGE_UNRESOLVED = "CHALLENGE_UNRESOLVED"


def is_unresolved(d):
    """True when the Red Team raised objections but the challenge could not finish (a model call failed before the Decision Maker answered).

    Older saved runs wrote NOT_CHALLENGED for this case, so that status counts too whenever objections exist; with no objections
    NOT_CHALLENGED still means the Red Team review itself could not run.
    """
    status = (d.get("outcome") or {}).get("status")
    return bool(d.get("objections")) and status in (CHALLENGE_UNRESOLVED, "NOT_CHALLENGED")


def unresolved_label(d):
    objs = d["objections"]
    n = len(objs)
    sev = ", ".join(f"{sum(1 for o in objs if o['severity'] == s)} {s}" for s in ("high", "medium", "low") if any(o["severity"] == s for o in objs))
    error = str((d.get("outcome") or {}).get("error") or "")
    stopped = "response timed out" if "timed out" in error.lower() else "response did not finish"
    return (f"Red Team raised {n} objection{'s' if n != 1 else ''} ({sev}); the Decision Maker's {stopped}, so the challenge is unresolved. "
            "Treat this proposal as unreviewed.")


CLEARED, PENDING, CONTESTED = "cleared", "pending", "contested"
STATUS_LABEL = {CLEARED: "DECISION CLEARED", PENDING: "DECISION PENDING REVIEW", CONTESTED: "DECISION CONTESTED"}
WHY_PROVISIONAL = "Red Team review did not complete within the runtime budget; not cleared for CFO action."


def review_state(d):
    """CLEARED when the Red Team loop completed (status RECOMMENDED), CONTESTED when it completed but a high-severity objection still
    stands, PENDING for everything else (timed out, unresolved, unreviewed, budget hit). None when there is no decision version."""
    if not d or not d.get("versions"):
        return None
    status = (d.get("outcome") or {}).get("status")
    return CLEARED if status == "RECOMMENDED" else CONTESTED if status == "CONTESTED" else PENDING


def provisional_line(kind, share_pct):
    return f"Provisional recommendation: {describe(kind, share_pct)}. Why provisional: {WHY_PROVISIONAL}"


def options_table_text(rows):
    """Plain-text table used inside model prompts. A price lock row carries its risk protected instead of a GBP cost."""
    return "\n".join(
        (f"{r['kind']} {r['share_pct']}% | {LOCK_COST_LINE} | {r['risk_line']} | {LOCK_TRADE_OFF}"
         if r["premium_gbp"] is None else
         f"{r['kind']} {r['share_pct']}% | premium {gbp(r['premium_gbp'])} | worst {gbp(r['worst'])} | likely {gbp(r['likely'])} | "
         f"best {gbp(r['best'])} | if forecast wrong {gbp(r['forecast_wrong'])}") for r in rows)
