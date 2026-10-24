"""Number consistency: AI text that quotes a premium different from the Python-calculated one gets a note. Uses the saved D3 focused run
(run 334f4e92). No model call; the AI's own text is never edited."""

import json
from pathlib import Path

import pytest

from core import dashboard
from core import decisions as dec
from core.evidence_page import build_evidence_page

RUN = Path(__file__).resolve().parents[1] / "output_examples" / "d3_focused_run_20261024"
NOTE = "Note: AI text quotes £2,469; the calculated premium is £4,939 (Python). The calculated figure is the one used."
pytestmark = pytest.mark.skipif(not (RUN / "world_state_334f4e92.json").exists(), reason="saved D3 focused run not present")


@pytest.fixture(scope="module")
def run():
    return dashboard.load_run(RUN, "D3 focused run")


def test_python_premium_is_spend_times_share_times_assumed_pct(run):
    item = next(i for i in run["report"]["items"] if i["impact"])
    spend = item["impact"]["baseline_quarterly_spend_gbp"]
    v = run["report"]["decisions"]["D3"]["versions"][0]
    assert (spend, v["kind"], v["share_pct"], dec.PREMIUM_PCT["HEDGE"]) == (329263.91, "HEDGE", 100, 1.5)  # shown as £329,264
    assert v["premium_gbp"] == 4939 == round(spend * 1.0 * 1.5 / 100)  # 4,938.96


def test_quoted_premiums_ignore_scenario_figures():
    assert dec.quoted_premiums("HEDGE 100% locks +£39,512 exposure at 1.5% premium (£2,469), protecting") == [2469]
    assert dec.quoted_premiums("Hedge locks £4,939 cost while foregoing £19,756 market gain") == [4939]
    assert dec.quoted_premiums("Protection premium £4,939 versus £19,756 no-action exposure") == [4939]
    assert dec.quoted_premiums("a £19,756 likely cost increase and £39,512 worst case") == []


def test_the_run_gets_exactly_one_note_for_the_decision_maker_figure(run):
    d = run["report"]["decisions"]["D3"]
    assert dec.premium_notes(329263.91, d) == [NOTE]
    assert "£2,469" in d["versions"][0]["rationale"]  # original AI text untouched


def test_matching_figures_and_locks_get_no_note():
    d = {"versions": [{"kind": "HEDGE", "share_pct": 50, "rationale": "premium of £2,469 on the protected half"}], "objections": []}
    assert dec.premium_notes(329264.0, d) == []
    d["versions"][0]["kind"] = "LOCK_PRICE"
    d["versions"][0]["rationale"] = "premium £6,585"
    assert dec.premium_notes(329264.0, d) == []


def test_note_is_in_the_evidence_trail_and_the_dashboard(run):
    page = build_evidence_page(run["report"], run["world"], {"red_team": True}, {"llm_ok": 6, "llm_fail": 2}, "2026-10-24", "t.jsonl")
    assert NOTE in page and "£2,469" in page and "£4,939" in page
    assert NOTE in dashboard.build_dashboard_html([run])
    assert (RUN / "evidence_trail.html").read_text(encoding="utf-8").count(NOTE) == 1


def test_dashboard_switcher_order_and_default():
    labels = [label for label, _ in dashboard.RUNS]
    assert labels[:3] == ["v3 run", "3-driver run", "D3 focused run"]
    html = (Path(__file__).resolve().parents[1] / "output_examples" / "dashboard.html").read_text(encoding="utf-8")
    assert html.index("D3 focused run") > html.index("3-driver run") and "Latest run" not in html
    assert 'data-default="1">v3 run' in html


def test_the_saved_run_files_match_the_trace(run):
    steps = [json.loads(l) for l in (RUN / "run_20261024_233755.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {s["run_id"] for s in steps} == {"334f4e92"}
    r = run["report"]["run"]
    assert (r["drivers"], r["elapsed_seconds"], r["llm_ok"], r["llm_fail"]) == (["D3"], 751, 6, 2)
    assert run["report"]["decisions"]["D3"]["outcome"]["status"] == "RECOMMENDED"
