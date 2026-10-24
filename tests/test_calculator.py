"""core/calculator.py: GBP is calculated in Python from purchase rows + a dated FX rate + explicit assumptions. No network, no Zetaris."""

import pytest

from core import calculator as calc
from core.brief import _calc_section, _why_it_matters
from core.claims import Claim

ASSUMPTIONS = {"SCENARIO_LARGE_MOVE_PCT": 12.0, "SCENARIO_LIKELY_MOVE_PCT": 6.0, "SCENARIO_SMALL_MOVE_PCT": 2.0, "FX_PASS_THROUGH_PCT": 100.0}
# 1,000 units x 1,000 USD at 1 GBP = 1.25 USD  ->  GBP 800,000
ROWS = [calc.PurchaseRow("P001", "GPU", "Taiwan", 1000, 1000.0, "USD", "2026-Q3", observed_total_gbp=767000.0, row_rate_gbp_per_unit=0.767)]


def claim(direction="cost_up", label="", low=0.0, high=0.0):
    return Claim("D1", "x", "P001", "GPU", "Taiwan", direction, low, high, [], pct_label=label)


def test_baseline_is_quantity_times_price_over_the_fx_rate():
    imp = calc.compute(ROWS, claim(), 1.25, "2026-10-23", "Frankfurter", ASSUMPTIONS)
    assert imp.baseline_quarterly_spend_gbp == 800000.0
    assert imp.calc["baseline_gbp"] == 800000.0 and imp.calc["baseline_label"] == "OBSERVED"


def test_scenario_range_comes_from_the_assumptions_when_no_source_states_a_percentage():
    imp = calc.compute(ROWS, claim(), 1.25, "2026-10-23", "Frankfurter", ASSUMPTIONS)
    assert (imp.worst_gbp, imp.likely_gbp, imp.best_gbp) == (96000.0, 48000.0, 16000.0)  # 12% / 6% / 2% of 800,000
    assert imp.pct_label == "SCENARIO" and (imp.pct_low, imp.pct_high) == (2.0, 12.0)
    assert imp.calc["result_gbp"]["note"] == "scenario range, not a probability"


def test_a_sourced_percentage_is_labelled_forecast_and_used_as_stated():
    imp = calc.compute(ROWS, claim(label="FORECAST", low=3, high=9), 1.25, "2026-10-23", "x", ASSUMPTIONS)
    assert imp.pct_label == "FORECAST" and (imp.worst_gbp, imp.likely_gbp, imp.best_gbp) == (72000.0, 48000.0, 24000.0)  # 9 / 6 / 3 %


def test_fx_moves_are_observed_and_pass_through_is_a_scenario_assumption():
    imp = calc.compute(ROWS, claim(label="OBSERVED", low=2, high=10), 1.25, "2026-10-23", "x", {**ASSUMPTIONS, "FX_PASS_THROUGH_PCT": 50.0})
    assert imp.pct_label == "OBSERVED" and imp.worst_gbp == 40000.0  # 10% x 50% of 800,000
    assert imp.calc["pass_through"] == {"pct": 50.0, "label": "SCENARIO"}


def test_cost_down_is_a_saving_and_worst_is_the_smallest_saving():
    imp = calc.compute(ROWS, claim("cost_down"), 1.25, "2026-10-23", "x", ASSUMPTIONS)
    assert (imp.worst_gbp, imp.likely_gbp, imp.best_gbp) == (-16000.0, -48000.0, -96000.0)


def test_calculation_record_has_inputs_fx_formula_and_assumptions():
    rec = calc.compute(ROWS, claim(), 1.25, "2026-10-23", "Frankfurter", ASSUMPTIONS).calc
    assert rec["inputs"][0] == {"part_id": "P001", "part_name": "GPU", "supplier_country": "Taiwan", "quantity": 1000,
                                "unit_price": 1000.0, "currency": "USD", "quarter": "2026-Q3", "label": "OBSERVED"}
    assert rec["fx"] == {"currency": "USD", "units_per_gbp": 1.25, "date": "2026-10-23", "source": "Frankfurter", "label": "OBSERVED"}
    assert "quantity x unit_price" in rec["formula"] and rec["assumptions"] == {"SCENARIO_LARGE_MOVE_PCT": 12.0, "SCENARIO_LIKELY_MOVE_PCT": 6.0, "SCENARIO_SMALL_MOVE_PCT": 2.0}
    assert rec["observed_total_gbp_in_zetaris"] == 767000.0


def test_bad_input_is_rejected():
    with pytest.raises(ValueError):
        calc.compute(ROWS, claim("sideways"), 1.25, "d", "s", ASSUMPTIONS)
    with pytest.raises(ValueError):
        calc.compute([], claim(), 1.25, "d", "s", ASSUMPTIONS)
    with pytest.raises(ValueError):
        calc.compute(ROWS, claim(), 0, "d", "s", ASSUMPTIONS)
    with pytest.raises(ValueError):
        calc.compute(ROWS, claim(label="FORECAST", low=9, high=3), 1.25, "d", "s", ASSUMPTIONS)


def test_shipped_assumptions_file_has_the_scenario_rows():
    a = calc.load_assumptions()
    assert all(k in a for k in calc.REQUIRED_ASSUMPTIONS) and a["SCENARIO_SMALL_MOVE_PCT"] < a["SCENARIO_LIKELY_MOVE_PCT"] < a["SCENARIO_LARGE_MOVE_PCT"]


def test_missing_assumption_is_an_error(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("option,premium_pct,note\nHEDGE,1.5,x\n", encoding="utf-8")
    with pytest.raises(ValueError):
        calc.load_assumptions(f)


def test_service_uses_live_fx_then_falls_back_to_the_row_rate_and_says_so():
    svc = calc.CalcService(None, "2026-Q3", fx_fn=lambda cur, d: (1.25, "2026-10-23"), assumptions=ASSUMPTIONS, rows_fn=lambda zt, p, q: ROWS)
    imp = svc.for_claim(claim())
    assert imp.calc["fx"]["date"] == "2026-10-23" and imp.baseline_quarterly_spend_gbp == 800000.0

    def down(cur, d):
        raise ConnectionError("no network")

    fallback = calc.CalcService(None, "2026-Q3", fx_fn=down, assumptions=ASSUMPTIONS, rows_fn=lambda zt, p, q: ROWS).for_claim(claim())
    assert fallback.calc["fx"]["source"].startswith("rate stored on the Zetaris purchase row")
    assert fallback.calc["fx"]["units_per_gbp"] == pytest.approx(1 / 0.767, rel=1e-5)


def test_wrong_hedge_cost_is_premium_plus_the_likely_move_given_away():
    imp = {"baseline_quarterly_spend_gbp": 800000.0, "likely_gbp": 48000.0}
    assert calc.wrong_hedge_cost_gbp(imp, 50, premium_pct=1.5) == round(800000 * 0.5 * 0.015 + 0.5 * 48000)


def test_brief_shows_the_why_line_and_a_calculation_section():
    impact = calc.compute(ROWS, claim(), 1.25, "2026-10-23", "Frankfurter", ASSUMPTIONS)
    item = {"attempts": [{"claim": Claim("D1", "Curbs raise GPU costs.", "P001", "GPU", "Taiwan", "cost_up", 2, 12, [], driver="Taiwan export curbs",
                                         pct_label="SCENARIO")}],
            "impact": {**impact.__dict__, "likely_gbp": impact.likely_gbp, "baseline_quarterly_spend_gbp": impact.baseline_quarterly_spend_gbp}}
    why = _why_it_matters([item])
    assert why.startswith("**Why this matters:**") and "£" in why and "SCENARIO" in why
    text = "\n".join(_calc_section(item))
    assert "How this number was calculated" in text and "1 GBP = 1.25 USD on 2026-10-23" in text and "OBSERVED" in text
    assert "scenario range" in text.lower() and "SCENARIO_LARGE_MOVE_PCT = 12" in text
    assert "no claim passed" in _why_it_matters([]).lower()


def test_assumptions_recorded_are_only_the_ones_used():
    scenario = calc.compute(ROWS, claim(), 1.25, "2026-10-23", "F", ASSUMPTIONS).calc["assumptions"]
    observed = calc.compute(ROWS, claim(label="OBSERVED", low=1.0, high=3.0), 1.25, "2026-10-23", "F", ASSUMPTIONS).calc["assumptions"]
    forecast = calc.compute(ROWS, claim(label="FORECAST", low=1.0, high=3.0), 1.25, "2026-10-23", "F", ASSUMPTIONS).calc["assumptions"]
    assert set(scenario) == {"SCENARIO_LARGE_MOVE_PCT", "SCENARIO_LIKELY_MOVE_PCT", "SCENARIO_SMALL_MOVE_PCT"}
    assert observed == {"FX_PASS_THROUGH_PCT": 100.0}  # an observed move shows no 12 / 6 / 2 scenario percentages
    assert forecast == {}


def test_fx_pair_must_match_the_currency_of_the_zetaris_row():
    from agents.evaluator import fx_pair_findings
    eur_rows = [calc.PurchaseRow("P006", "Cooling", "Germany", 291, 1896.47, "EUR", "2026-Q3")]
    usd_rows = [calc.PurchaseRow("P006", "Cooling", "Germany", 291, 2150.0, "USD", "2026-Q3")]
    eur_claim = Claim("D2", "x", "P006", "Cooling", "Germany", "cost_up", 1.0, 2.0, [], currency="EUR")
    assert fx_pair_findings(eur_claim, calc.compute(eur_rows, eur_claim, 1.17, "2026-10-23", "F", ASSUMPTIONS).calc) == []
    found = fx_pair_findings(eur_claim, calc.compute(usd_rows, eur_claim, 1.33, "2026-10-23", "F", ASSUMPTIONS).calc)
    assert [f.code for f in found] == ["FX_PAIR_MISMATCH"] and "GBP/EUR" in found[0].message and "USD" in found[0].message


def test_p006_is_priced_in_eur_in_the_synthetic_purchase_data():
    import csv
    from core import config
    rows = list(csv.DictReader(open(config.ROOT / "data" / "purchases.csv", newline="", encoding="utf-8")))
    assert {r["currency"] for r in rows if r["part_id"] == "P006"} == {"EUR"}
    assert {r["currency"] for r in rows if r["part_id"] != "P006"} == {"USD"}
    latest = [r for r in rows if r["part_id"] == "P006"][-1]  # still equals the recorded GBP total
    assert abs(float(latest["quantity"]) * float(latest["unit_price"]) * float(latest["gbp_per_currency_unit"]) - float(latest["total_gbp"])) < 1


def test_no_hedge_when_the_worst_case_is_a_saving():
    from agents.decision_maker import DecisionMaker, apply_responses
    from core import decisions
    saving = {"worst_gbp": -1000.0, "likely_gbp": -3000.0, "best_gbp": -6000.0}
    increase = {"worst_gbp": 6000.0, "likely_gbp": 3000.0, "best_gbp": 1000.0}
    kind, share, why = decisions.rule_choice(saving)
    assert (kind, share) == ("NO_ACTION", 0) and "not a cost increase" in why
    assert decisions.rule_choice(increase) is None

    class NoModel(DecisionMaker):  # a model call here would be a failure of the rule
        def __init__(self):
            self.name = "decision_maker"
            self.trace = type("T", (), {"event": lambda *a, **k: None})()

        def _ask(self, *a, **k):
            raise AssertionError("the model must not be called when the rule fires")

    ev = {"work_id": "D2", "spend_gbp": 100000.0, "impact": saving}
    for aggressive in (False, True):  # even the aggressive demo cannot hedge a saving
        d = NoModel().propose(ev, aggressive=aggressive)
        assert (d["kind"], d["source"]) == ("NO_ACTION", "rule") and d["premium_gbp"] == 0
    first = NoModel().propose(ev)
    ev = {**ev, "sources": [], "claim": "c", "part_id": "P006", "part_name": "Cooling", "direction": "cost_down", "pct_low": 1, "pct_high": 2,
          "confidence": "Medium", "strength_line": "", "analyst": "currency", "retries": 0, "strength": "",
          "concentration": {"total_spend_gbp": 1, "part_share_pct": 1, "country": "Germany", "country_share_pct": 1, "supplier": "S", "supplier_share_pct": 1}}
    objection = [{"id": "O1", "severity": "high", "type": "x", "text": "t", "evidence_refs": [], "suggested_change": ""}]
    reply = {"responses": [{"objection_id": "O1", "action": "REVISE", "reason": "r", "evidence_refs": ["£100,000"]}],
             "revised_decision": {"kind": "HEDGE", "share_pct": 50, "rationale": "r"}}
    assert apply_responses(ev, first, objection, reply)[1] is None  # a revision cannot reintroduce a hedge
