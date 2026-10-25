"""Freeze changes: the price lock has no invented cost, the default scenario band is labelled, the rule wording, the D3 prompt. No model call."""

import re
from pathlib import Path

import pytest

from agents import supplier_commodity
from agents.decision_maker import make_decision
from core import calculator, dashboard, decisions
from core.red_team import checklist, evidence_block, red_team_block

ROOT = Path(__file__).resolve().parents[1]
IMPACT = {"worst_gbp": 39512.0, "likely_gbp": 19756.0, "best_gbp": 6585.0, "baseline_quarterly_spend_gbp": 329264.0, "confidence": "Medium"}
EV = {"work_id": "D3", "analyst": "supplier_commodity", "part_id": "P002", "part_name": "HBM Stack", "claim": "c", "direction": "cost_up",
      "pct_low": 2.0, "pct_high": 12.0, "confidence": "Medium", "strength": "", "retries": 0, "strength_line": "x", "spend_gbp": 329264.0,
      "impact": IMPACT, "sources": [{"id": "S1", "publisher": "Reuters", "tier": 1, "date": "2026-10-20", "title": "t"}],
      "concentration": {"total_spend_gbp": 2500000, "part_share_pct": 12.9, "country": "South Korea", "country_share_pct": 12.9,
                        "supplier": "K", "supplier_share_pct": 12.9}}


def test_no_lock_premium_is_assumed():
    assert "LOCK_PRICE" not in decisions.PREMIUM_PCT and "MEMORY_SCENARIO" not in (ROOT / "data" / "decision_assumptions.csv").read_text()
    c = decisions.option_cost(329264.0, IMPACT, "LOCK_PRICE", 100)
    assert c["premium_gbp"] is None and all(c[k] is None for k in decisions.SCENARIOS)
    assert c["risk_protected_gbp"] == 39512  # worst case x locked share
    assert decisions.option_cost(329264.0, IMPACT, "LOCK_PRICE", 50)["risk_protected_gbp"] == 19756
    assert decisions.option_cost(329264.0, IMPACT, "HEDGE", 50)["premium_gbp"] == round(329264 * 0.5 * 0.015)  # documented premium kept


def test_lock_lines_are_computed_not_reused():
    full = decisions.lock_lines(IMPACT, 100)
    assert full[0] == "Cost of action: Not quantified - requires a supplier quote for the price-lock premium and volume commitment."
    assert full[1] == "Risk protected: protects against the full worst-case exposure of £39,512."
    assert full[2] == "Trade-off: locking may forgo savings if prices soften."
    assert full[3].startswith("What would change this recommendation: a supplier quote showing the lock premium exceeds")
    assert decisions.lock_lines(IMPACT, 50)[1] == "Risk protected: protects against 50% of the worst-case exposure, up to £19,756."
    assert "6,585" not in " ".join(full)


def test_lock_decision_reaches_the_prompts_without_a_gbp_cost():
    d = make_decision(329264.0, IMPACT, "LOCK_PRICE", 100, "r", 1, "model")
    rows = decisions.compare_options(329264.0, IMPACT, ("LOCK_PRICE", 100))
    for text in (evidence_block(EV, rows, d), red_team_block(EV, d), decisions.options_table_text(rows)):
        assert "Not quantified" in text and "£39,512" in text
        assert "lock 2" not in text and "price lock 2" not in text
    lock_row = next(line for line in decisions.options_table_text(rows).splitlines() if line.startswith("LOCK_PRICE"))
    assert "£6,585" not in lock_row and not re.search(r"premium [+-]?£", lock_row)
    assert "Not quantified" in checklist(EV, rows, d)  # the Red Team is told the cost is unquantified instead of being given a figure


def test_rule_wording_is_not_a_blanket_ban():
    cost_down = {"worst_gbp": -100.0, "likely_gbp": -200.0, "best_gbp": -300.0}
    assert decisions.rule_choice(cost_down)[2].count("cost down") == 1
    for doc in ("README.md", "docs/JUDGE_QA.md"):
        assert "no hedge or price lock when the verified expected direction is cost down" in (ROOT / doc).read_text(encoding="utf-8").lower()
    assert "no hedge or price lock when the verified expected direction is cost down" in decisions.rule_choice.__doc__.lower()


def test_band_label():
    note = calculator.band_note(2.0, 12.0)
    assert note.startswith("2-12% is Ganga's default SCENARIO band for a verified directional cost driver with no directly comparable, "
                           "quantified forecast for this part and quarter. It is not a probability and is not part-specific.")
    assert "The 2-12% default" not in note


def test_d3_prompt_steers_the_argument():
    p = supplier_commodity.CLAIM_FORMAT
    for needle in ("persistent HBM supply constraints", "standard DRAM", "wafer capacity", "supplier pricing power", "default",
                   "no part-specific quarterly price forecast", "cited, fetched articles", "cite only the listed ids"):
        assert needle.lower() in p.lower()


@pytest.mark.skipif(not (ROOT / "output_examples" / "live_run_20261024_3drivers" / "world_state_d9decb7d.json").exists(), reason="no saved run")
def test_saved_3driver_run_shows_label_and_honest_lock():
    runs = dashboard.default_runs()
    assert [r["label"] for r in runs] == ["v3 run", "3-driver run", "D3 focused run", "Live run - could not verify", "25 Oct run - D1 contested"]
    page = dashboard.build_dashboard_html(runs)
    sec = re.search(r'<div class="run" id="run-d9decb7d">(.*?)(?=<div class="run" id=|<footer>)', page, re.S)
    assert sec and "Latest run" not in page
    body = sec.group(1)
    assert "default SCENARIO band" in body and "Memory exposure is driven by industry capacity allocation" in body
    assert "Cost of action: Not quantified" in body and "Risk protected: protects against the full worst-case exposure of £39,512" in body
    assert "3 objections" in body or "3 objection(s) raised" in body
