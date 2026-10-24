"""Decision status labels: CLEARED only when the Red Team loop completed; everything else is PENDING REVIEW with a provisional action.
Derived from run data in code; no model call."""

import re
import types
from pathlib import Path

import pytest

from core import brief, dashboard
from core import decisions as dec
from core.evidence_page import build_evidence_page

BASE = Path(__file__).resolve().parents[1] / "output_examples"
THREE = BASE / "live_run_20261024_3drivers"
PROVISIONAL = ("Provisional recommendation: {action}. Why provisional: Red Team review did not complete within the runtime budget; "
               "not cleared for CFO action.")


def _d(status, versions=True):
    return {"versions": [{"kind": "HEDGE", "share_pct": 50}] if versions else [], "objections": [], "outcome": {"status": status}}


def test_review_state_is_derived_from_the_outcome():
    assert dec.review_state(_d("RECOMMENDED")) == dec.CLEARED
    assert dec.review_state(_d("CONTESTED")) == dec.CONTESTED
    for status in ("NOT_CHALLENGED", "UNCHALLENGED", "CHALLENGE_UNRESOLVED", "NOT_DECIDED", None):
        assert dec.review_state(_d(status)) == dec.PENDING
    assert dec.review_state(_d("RECOMMENDED", versions=False)) is None and dec.review_state(None) is None
    assert dec.STATUS_LABEL[dec.CLEARED] == "DECISION CLEARED" and dec.STATUS_LABEL[dec.PENDING] == "DECISION PENDING REVIEW"


def test_provisional_line_wording_is_exact():
    assert dec.provisional_line("NO_ACTION", 0) == PROVISIONAL.format(action="No action, monitor")


def _brief_text(outcome):
    version = {"version": 1, "kind": "HEDGE", "share_pct": 50, "rationale": "because", "cost": {k: 0 for k in dec.SCENARIOS}}
    claim = types.SimpleNamespace(part_name="GPU Chip", supplier_country="Taiwan")
    impact = {"baseline_quarterly_spend_gbp": 100000.0, "worst_gbp": 8000.0, "likely_gbp": 5000.0, "best_gbp": 2000.0}
    item = {"delegation": {"work_id": "D1", "status": "PASS", "action": "run"}, "attempts": [{"claim": claim}], "impact": impact}
    report = {"decisions": {"D1": {"versions": [version], "objections": [], "outcome": outcome}}}
    return "\n".join(brief._decisions_section(report, [item])), report


def test_brief_cleared_and_pending_headings():
    cleared, _ = _brief_text({"status": "RECOMMENDED", "rounds": 1})
    assert "- DECISION CLEARED" in cleared and "PENDING" not in cleared and "Provisional" not in cleared
    pending, report = _brief_text({"status": "NOT_CHALLENGED", "error": "timeout", "rounds": 0})
    assert "- DECISION PENDING REVIEW" in pending and "CLEARED" not in pending
    assert PROVISIONAL.format(action="Hedge a share of next quarter's spend: 50%") in pending
    assert "[DECISION PENDING REVIEW] D1: Provisional recommendation:" in brief.console_summary(
        {"items": [], **report})


@pytest.mark.skipif(not (THREE / "world_state_d9decb7d.json").exists(), reason="saved 3-driver run not present")
def test_three_driver_run_d1_d3_pending_d2_cleared():
    run = dashboard.load_run(THREE, "3-driver run")
    decs = run["report"]["decisions"]
    assert {w: dec.review_state(d) for w, d in decs.items()} == {"D1": dec.PENDING, "D2": dec.CLEARED, "D3": dec.PENDING}
    page = dashboard.build_dashboard_html([run])
    cards = {w: re.search(rf'<div class="card ([a-z]+)"[^>]*data-chain="[^"]*-{w}">(.*?)</div><div class="hint">', page, re.S) for w in ("D1", "D2", "D3")}
    assert [cards[w].group(1) for w in ("D1", "D2", "D3")] == ["pending", "cleared", "pending"]
    for w in ("D1", "D3"):
        assert "DECISION PENDING REVIEW" in cards[w].group(2) and "Provisional recommendation:" in cards[w].group(2)
        assert "Why provisional: " + dec.WHY_PROVISIONAL in cards[w].group(2)
    assert "DECISION CLEARED" in cards["D2"].group(2) and "Provisional" not in cards["D2"].group(2)
    assert ".card.pending{border-left:6px solid var(--warn)}" in page and ".card.cleared{border-left:6px solid var(--teal)}" in page
    assert "2 decisions pending review" in page and "1 decision cleared by Red Team" in page


@pytest.mark.skipif(not (THREE / "world_state_d9decb7d.json").exists(), reason="saved 3-driver run not present")
def test_evidence_page_labels_the_three_driver_run():
    run = dashboard.load_run(THREE, "3-driver run")
    html = build_evidence_page(run["report"], run["world"])
    assert html.count("DECISION PENDING REVIEW") >= 4 and html.count("DECISION CLEARED") >= 2  # summary row + final step, per decision
    assert "Provisional recommendation:" in html
