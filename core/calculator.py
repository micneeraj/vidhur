"""GBP impact, calculated in plain Python. The AI never produces a GBP figure; every GBP number in the CFO brief comes from here.

Inputs are Ganga's purchase rows from Zetaris (part, supplier country, quantity, unit price, currency), a real FX rate with its
date, and the explicit assumptions in data/decision_assumptions.csv. The result carries a calculation record (inputs, FX rate +
date, formula, assumptions) so any figure can be re-derived by hand.

Every number is labelled:
  OBSERVED  read from data or a data source (Zetaris rows, ECB rates)
  FORECAST  stated by a cited source (a percentage an article actually prints)
  SCENARIO  our own assumption (data/decision_assumptions.csv)
The worst / likely / best figures are a scenario range, not a probability.

Sign convention: positive = extra cost for Ganga, negative = a saving. "worst" is the most expensive outcome, "best" the cheapest.
"""

import csv
import threading
from dataclasses import dataclass

from core import config
from core.impact import Impact

OBSERVED, FORECAST, SCENARIO = "OBSERVED", "FORECAST", "SCENARIO"
LABELS = (OBSERVED, FORECAST, SCENARIO)
ASSUMPTIONS_FILE = config.ROOT / "data" / "decision_assumptions.csv"
REQUIRED_ASSUMPTIONS = ("SCENARIO_LARGE_MOVE_PCT", "SCENARIO_LIKELY_MOVE_PCT", "SCENARIO_SMALL_MOVE_PCT", "FX_PASS_THROUGH_PCT")
FORMULA = ("baseline_gbp = sum(quantity x unit_price / units_of_currency_per_GBP) over the input rows; "
           "change_gbp = baseline_gbp x move_pct / 100 x pass_through_pct / 100, "
           "signed + for cost_up and - for cost_down; worst/likely/best use the large/likely/small move "
           "(for cost_down the small move is the worst case, because it saves least)")
RANGE_NOTE = "scenario range, not a probability"


@dataclass
class PurchaseRow:
    part_id: str
    part_name: str
    supplier_country: str
    quantity: float
    unit_price: float
    currency: str
    quarter: str
    observed_total_gbp: float = 0.0  # what Zetaris records for the row at the historical rate (reference only)
    row_rate_gbp_per_unit: float = 0.0  # the historical GBP-per-unit-of-currency rate stored on the row (fallback FX)

    def to_dict(self):
        return {"part_id": self.part_id, "part_name": self.part_name, "supplier_country": self.supplier_country,
                "quantity": self.quantity, "unit_price": self.unit_price, "currency": self.currency, "quarter": self.quarter,
                "label": OBSERVED}


def load_assumptions(path=ASSUMPTIONS_FILE):
    """{name: value} from data/decision_assumptions.csv. Raises if a scenario assumption the calculator needs is missing."""
    with open(path, newline="", encoding="utf-8") as f:
        values = {row["option"].strip(): float(row["premium_pct"]) for row in csv.DictReader(f)}
    missing = [k for k in REQUIRED_ASSUMPTIONS if k not in values]
    if missing:
        raise ValueError(f"{path} has no value for: {', '.join(missing)}")
    return values


def percentage_basis(claim, assumptions):
    """(label, small, likely, large, pass_through_pct, source_text): the move sizes behind a claim, magnitudes in percent.

    A sourced percentage (label FORECAST) or one computed from FX data (OBSERVED) is used as stated, with likely = midpoint.
    Otherwise (SCENARIO, including every claim whose analyst gave no sourced number) the range is our assumption.
    """
    label = (claim.pct_label or "").upper()
    pass_through = 100.0
    if label == OBSERVED:
        pass_through = assumptions["FX_PASS_THROUGH_PCT"]
    if label in (FORECAST, OBSERVED):
        small, large = float(claim.pct_low), float(claim.pct_high)
        return label, small, round((small + large) / 2, 4), large, pass_through, (
            "percentage range stated by a cited source" if label == FORECAST else "FX move computed from ECB reference rates")
    return (SCENARIO, assumptions["SCENARIO_SMALL_MOVE_PCT"], assumptions["SCENARIO_LIKELY_MOVE_PCT"],
            assumptions["SCENARIO_LARGE_MOVE_PCT"], pass_through,
            "no source states a percentage; scenario assumption from data/decision_assumptions.csv")


def compute(rows, claim, units_per_gbp, fx_date, fx_source, assumptions):
    """Impact (with its calculation record) for one claim on one part. Pure function: no I/O.

    rows:           PurchaseRow list for the claim's part in the baseline quarter
    units_per_gbp:  units of the rows' currency that 1 GBP buys on fx_date (e.g. 1.3368 USD per GBP)
    """
    if claim.direction not in ("cost_up", "cost_down"):
        raise ValueError(f"direction must be cost_up or cost_down, got {claim.direction!r}")
    if not rows:
        raise ValueError(f"no purchase rows for {claim.part_id}")
    if units_per_gbp <= 0:
        raise ValueError("FX rate must be positive")
    label, small, likely, large, pass_through, basis = percentage_basis(claim, assumptions)
    if small < 0 or large < small:
        raise ValueError("percent range must satisfy 0 <= small <= large")

    baseline = sum(r.quantity * r.unit_price / units_per_gbp for r in rows)
    factor = pass_through / 100
    change = lambda pct: baseline * pct / 100 * factor  # noqa: E731
    if claim.direction == "cost_up":
        worst, mid, best = change(large), change(likely), change(small)
    else:
        worst, mid, best = -change(small), -change(likely), -change(large)

    record = {
        "inputs": [r.to_dict() for r in rows],
        "fx": {"currency": rows[0].currency, "units_per_gbp": round(units_per_gbp, 6), "date": fx_date, "source": fx_source,
               "label": OBSERVED},
        "baseline_gbp": round(baseline, 2), "baseline_label": OBSERVED,
        "observed_total_gbp_in_zetaris": round(sum(r.observed_total_gbp for r in rows), 2),
        "move_pct": {"small": small, "likely": likely, "large": large, "label": label, "basis": basis},
        "pass_through": {"pct": pass_through, "label": SCENARIO if label == OBSERVED else "n/a"},
        "formula": FORMULA, "direction": claim.direction,
        "result_gbp": {"worst": round(worst, 2), "likely": round(mid, 2), "best": round(best, 2), "label": SCENARIO,
                       "note": RANGE_NOTE},
        "assumptions": {k: assumptions[k] for k in REQUIRED_ASSUMPTIONS},
    }
    return Impact(baseline_quarterly_spend_gbp=round(baseline, 2), worst_gbp=round(worst, 2), likely_gbp=round(mid, 2),
                  best_gbp=round(best, 2), pct_low=small, pct_high=large, direction=claim.direction, pct_label=label, calc=record)


def wrong_hedge_cost_gbp(impact, share_pct=50, premium_pct=None):
    """What a hedge of `share_pct` of the part's spend costs Ganga if the forecast is wrong: the premium paid plus the share of the
    likely move that the hedge gives away. A SCENARIO figure computed from the calculator's baseline and likely case."""
    from core import decisions
    premium_pct = decisions.PREMIUM_PCT["HEDGE"] if premium_pct is None else premium_pct
    h = share_pct / 100
    return round(impact["baseline_quarterly_spend_gbp"] * h * premium_pct / 100 + h * abs(impact["likely_gbp"]))


class CalcService:
    """Fetches purchase rows (Zetaris) and the FX rate once, then calculates impacts for claims. Thread-safe.

    fx_fn(currency, date_or_None) -> (units_per_gbp, date). If it fails, the rate stored on the Zetaris row is used and the record says so.
    """

    def __init__(self, zt, quarter, fx_fn=None, as_of=None, assumptions=None, rows_fn=None):
        from core import fx
        from core.ganga_data import purchase_rows
        self.zt, self.quarter, self.as_of = zt, quarter, as_of
        self.fx_fn = fx_fn or fx.rate_on
        self.rows_fn = rows_fn or purchase_rows
        self.assumptions = assumptions or load_assumptions()
        self._rows, self._fx, self._lock = {}, {}, threading.Lock()

    def rows(self, part_id):
        with self._lock:
            if part_id not in self._rows:
                self._rows[part_id] = self.rows_fn(self.zt, part_id, self.quarter)
            return self._rows[part_id]

    def fx_for(self, rows):
        currency = rows[0].currency
        with self._lock:
            if currency in self._fx:
                return self._fx[currency]
        try:
            rate, day = self.fx_fn(currency, self.as_of)
            found = (rate, day, "Frankfurter (ECB reference rates)")
        except Exception as exc:  # network or API failure: fall back to the rate stored on the purchase row, and say so
            stored = rows[0].row_rate_gbp_per_unit
            if not stored:
                raise
            found = (1 / stored, rows[0].quarter,
                     f"rate stored on the Zetaris purchase row (live FX unavailable: {type(exc).__name__})")
        with self._lock:
            self._fx[currency] = found
        return found

    def for_claim(self, claim):
        rows = self.rows(claim.part_id)
        rate, day, source = self.fx_for(rows)
        return compute(rows, claim, rate, day, source, self.assumptions)
