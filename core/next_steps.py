"""'What would change this decision': confidence meaning, what would raise confidence, and when to re-run. Plain Python, no model.

Built only from data the run already holds: the claim's cited sources and their trust tiers, the Evaluator's last verdict, the FX
figures in the claim, and RERUN_FX_MOVE_PCT in data/decision_assumptions.csv. The re-run trigger is advice to a person; Vidhur does
not watch rates or news between runs.
"""

import csv

from core import config
from core.source_trust import trust_of

ASSUMPTIONS_FILE = config.ROOT / "data" / "decision_assumptions.csv"
CONFIDENCE_MEANING = ("Confidence reflects evidence quality and source agreement, not the size of the impact. "
                      "Low = investigate before committing money; High = fine for routine action or monitoring.")
UNVERIFIED_NEXT_STEP = ("Next step: find two independent sources (one Tier 1 or 2) before any decision; "
                        "no GBP figure until then.")
HIGH_NEEDS = 3  # independent sources for High, from core.impact.confidence_level


def rerun_fx_move_pct(path=ASSUMPTIONS_FILE):
    """RERUN_FX_MOVE_PCT from data/decision_assumptions.csv. Raises if the row is missing."""
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["option"].strip() == "RERUN_FX_MOVE_PCT":
                return float(row["premium_pct"])
    raise ValueError(f"{path} has no RERUN_FX_MOVE_PCT row")


def _tiers(world, claim):
    """Trust tier of every distinct cited source that is in the fetched evidence."""
    tiers = []
    for url in dict.fromkeys(claim.source_urls):
        art = world.article(url)
        if art:
            tiers.append(trust_of(url, art["publisher"])[1])
    return tiers


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def raise_confidence(claim, tiers, independent, strength, impact_label=None, sent_back=0):
    """What is missing from this claim's evidence, as one sentence. Says so when nothing is."""
    gaps = []
    if independent < HIGH_NEEDS:
        gaps.append(f"{_plural(HIGH_NEEDS - independent, 'more independent source')} (the Evaluator counted {independent}; "
                    f"{HIGH_NEEDS} or more give High)")
    best = min(tiers) if tiers else None
    if best is None or best > 1:
        gaps.append("an independent Tier 1 source" + (f" (the best cited is Tier {best})" if best else " (no cited source was in the fetched evidence)"))
    if strength == "weak":
        gaps.append("evidence that supports the claim more strongly (the Evaluator judged it weak)")
    if (impact_label or claim.pct_label) == "SCENARIO":
        gaps.append("a source that states the size of the move (the percentage is our SCENARIO assumption)")
    note = (f" Confidence is also one level lower because the claim was sent back {_plural(sent_back, 'time')} before it passed; "
            "that cannot be undone for this run." if sent_back else "")
    if not gaps:
        return (f"Nothing material is missing: {_plural(independent, 'independent source')} including a Tier 1 source, "
                "and the figures are read from data or a cited source." + note)
    return "What would raise confidence: " + "; ".join(gaps) + "." + note


def rerun_trigger(claim, impact):
    """When to run Vidhur again. Currency drivers: an FX move from the rate the run used; others: new Tier 1/2 news."""
    if claim.analyst == "currency" and claim.currency:
        comp = claim.computed or {}
        fx = ((impact or {}).get("calc") or {}).get("fx") or {}
        rate, date = comp.get("end_rate", fx.get("units_per_gbp")), comp.get("end_date", fx.get("date"))
        if rate is not None and date:
            return (f"Re-run if GBP/{claim.currency} moves more than {rerun_fx_move_pct():g}% from {rate:g} {claim.currency} per GBP "
                    f"on {date} (RERUN_FX_MOVE_PCT in data/decision_assumptions.csv). This is a manual trigger; Vidhur does not watch rates.")
    return f"Re-run on new Tier 1/2 news about {(claim.driver or claim.claim)[:90].rstrip()}. This is a manual trigger; Vidhur does not watch the news."


def for_decision(item, world):
    """{"confidence_meaning", "raise", "rerun"} for a verified item (needs item["impact"]), from the item's last attempt."""
    last = item["attempts"][-1]
    claim, impact = last["claim"], item["impact"]
    verdict = last["verdicts"][-1] if last["verdicts"] else {}
    independent = verdict.get("independent_sources", 0)
    return {"confidence_meaning": CONFIDENCE_MEANING,
            "raise": raise_confidence(claim, _tiers(world, claim), independent, verdict.get("strength", ""), impact.get("pct_label"),
                                   len(item["attempts"]) - 1),
            "rerun": rerun_trigger(claim, impact)}


def for_unverified():
    return {"confidence_meaning": CONFIDENCE_MEANING, "next_step": UNVERIFIED_NEXT_STEP}
