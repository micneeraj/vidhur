"""GBP impact maths. Done in plain Python from Zetaris data - never by the model.

Sign convention: a positive number is extra cost for Ganga, a negative number is a saving.
"worst" is always the outcome most expensive for Ganga, "best" the cheapest.
"""

from dataclasses import dataclass, field

CONFIDENCE_LEVELS = ["Low", "Medium", "High"]


@dataclass
class Impact:
    baseline_quarterly_spend_gbp: float
    worst_gbp: float
    likely_gbp: float
    best_gbp: float
    pct_low: float
    pct_high: float
    direction: str
    pct_label: str = ""  # FORECAST / OBSERVED / SCENARIO: where the percentage range comes from
    calc: dict = field(default_factory=dict)  # the calculation record (core/calculator.py): inputs, FX rate + date, formula, assumptions


def compute_impact(quarterly_spend_gbp, pct_low, pct_high, direction):
    """Quarterly spend x estimated % change range -> worst / likely / best change in quarterly cost."""
    if direction not in ("cost_up", "cost_down"):
        raise ValueError(f"direction must be cost_up or cost_down, got {direction!r}")
    if pct_low < 0 or pct_high < pct_low:
        raise ValueError("percent range must satisfy 0 <= pct_low <= pct_high")

    low = quarterly_spend_gbp * pct_low / 100
    high = quarterly_spend_gbp * pct_high / 100
    likely = (low + high) / 2
    if direction == "cost_up":
        worst, likely_v, best = high, likely, low
    else:  # cost_down: the biggest saving is the best case, the smallest saving the worst
        worst, likely_v, best = -low, -likely, -high
    return Impact(
        baseline_quarterly_spend_gbp=round(quarterly_spend_gbp, 2),
        worst_gbp=round(worst, 2),
        likely_gbp=round(likely_v, 2),
        best_gbp=round(best, 2),
        pct_low=pct_low,
        pct_high=pct_high,
        direction=direction,
    )


def confidence_level(independent_sources, retries, strength):
    """Confidence from the Evaluator result.

    Start from the number of independent sources (3+ -> High, 2 -> Medium), then drop one
    level if the claim only passed after being sent back, and one level if the Evaluator
    judged the evidence only weakly supportive. Never below Low.
    """
    level = 2 if independent_sources >= 3 else 1
    if retries > 0:
        level -= 1
    if strength == "weak":
        level -= 1
    return CONFIDENCE_LEVELS[max(level, 0)]
