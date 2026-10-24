"""'What would change this decision': deterministic confidence / re-run guidance, no model."""

import pytest

from core import brief, next_steps
from core.claims import Claim
from core.impact import Impact
from core.evidence_page import build_evidence_page
from core.news import Article
from core.shared_state import RunWorld


class FakeWorld:
    def __init__(self, arts):
        self.arts = arts

    def article(self, url):
        return self.arts.get(url)


ARTS = {"https://reuters.com/a": {"publisher": "Reuters", "title": "t", "published": "2026-10-20"},
        "https://unknownblog.example/b": {"publisher": "Some Blog", "title": "t", "published": "2026-10-20"}}


def claim(analyst="geopolitics", urls=("https://reuters.com/a",), label="SCENARIO", **kw):
    return Claim("D1", "Export curbs raise GPU costs.", "P001", "GPU", "Taiwan", "cost_up", 2, 12, list(urls), "r", 1, analyst,
                 driver=kw.pop("driver", "Taiwan export curbs"), pct_label=label, **kw)


def item(c, independent, strength="strong", attempts=1, confidence="Low", calc=None):
    verdict = {"verdict": "PASS", "independent_sources": independent, "strength": strength}
    return {"delegation": {"work_id": "D1", "status": "PASS"}, "attempts": [{"claim": c, "verdicts": [verdict]}] * attempts,
            "impact": {"confidence": confidence, "pct_label": c.pct_label, "calc": calc or {}}}


def test_rerun_fx_move_is_read_from_the_assumptions_csv(tmp_path):
    assert next_steps.rerun_fx_move_pct() == 2.0
    no_row = tmp_path / "a.csv"
    no_row.write_text("option,premium_pct,note\nHEDGE,1.5,x\n", encoding="utf-8")
    with pytest.raises(ValueError):
        next_steps.rerun_fx_move_pct(no_row)


def test_one_tier_1_source_asks_for_more_independent_sources_and_a_stated_percentage():
    g = next_steps.for_decision(item(claim(), 1), FakeWorld(ARTS))
    assert "2 more independent sources" in g["raise"] and "SCENARIO assumption" in g["raise"] and "Tier 1 source" not in g["raise"].split(";")[1]
    assert g["confidence_meaning"].startswith("Confidence reflects evidence quality and source agreement, not the size of the impact.")


def test_low_trust_sources_ask_for_a_tier_1_source():
    g = next_steps.for_decision(item(claim(urls=("https://unknownblog.example/b",), label="FORECAST"), 3), FakeWorld(ARTS))
    assert "an independent Tier 1 source (the best cited is Tier 3)" in g["raise"] and "more independent" not in g["raise"]


def test_nothing_missing_is_said_plainly_and_a_send_back_is_noted():
    c = claim(label="OBSERVED")
    g = next_steps.for_decision(item(c, 3), FakeWorld(ARTS))
    assert g["raise"].startswith("Nothing material is missing") and "sent back" not in g["raise"]
    assert "sent back 1 time" in next_steps.for_decision(item(c, 3, attempts=2), FakeWorld(ARTS))["raise"]


def test_weak_evidence_is_named():
    assert "judged it weak" in next_steps.for_decision(item(claim(label="FORECAST"), 3, strength="weak"), FakeWorld(ARTS))["raise"]


def test_currency_rerun_uses_the_rate_date_and_threshold_from_the_run():
    c = claim("currency", currency="EUR", label="OBSERVED", computed={"end_rate": 1.1943, "end_date": "2026-10-23"})
    text = next_steps.for_decision(item(c, 3), FakeWorld(ARTS))["rerun"]
    assert "GBP/EUR moves more than 2% from 1.1943 EUR per GBP on 2026-10-23" in text and "manual trigger" in text


def test_currency_rerun_falls_back_to_the_calculation_record():
    c = claim("currency", currency="EUR", label="OBSERVED")
    calc = {"fx": {"units_per_gbp": 1.25, "date": "2026-10-22"}}
    assert "from 1.25 EUR per GBP on 2026-10-22" in next_steps.rerun_trigger(c, {"calc": calc})


def test_geopolitics_rerun_is_about_new_tier_1_2_news():
    assert next_steps.rerun_trigger(claim(), {}).startswith("Re-run on new Tier 1/2 news about Taiwan export curbs")


def test_unverified_has_the_fixed_next_step_and_no_figure():
    g = next_steps.for_unverified()
    assert g["next_step"] == ("Next step: find two independent sources (one Tier 1 or 2) before any decision; no GBP figure until then.")


def test_the_page_and_the_brief_carry_the_section(tmp_path):
    world = RunWorld("n1", path=tmp_path / "w.json")
    world.start_run({"run_id": "n1"})
    world.record_articles([Article("Taiwan curbs chip exports", "Reuters", "https://reuters.com/a", "2026-10-20T00:00:00+00:00")], "GPU", "Taiwan", "geopolitics")
    world.record_plan({"summary": "s", "delegations": [{"analyst": "geopolitics", "part_id": "P001", "work_id": "D1", "reason": "x"},
                                                       {"analyst": "currency", "part_id": "P006", "work_id": "D2", "reason": "fx"}], "skipped": []}, [])
    c = claim()
    k = world.write_claim(c, "D1", "geopolitics")
    from core.claims import Verdict
    world.write_verdict(k, Verdict("PASS", ["ok"], 1, "strong", True, []), 0)
    world.write_final("D1", "PASS", 0)
    world.write_impact("D1", k, Impact(100, 10, 5, 1, 2, 12, "cost_up", "SCENARIO"), "Low", "2026-Q3")
    world.write_error("D2", "APIConnectionError: down")
    report = world.report()
    html = build_evidence_page(report, world)
    assert html.count("What would change this decision") == 2  # D1 (verified) and D2 (could not verify)
    assert "Re-run on new Tier 1/2 news about Taiwan export curbs" in html and "find two independent sources" in html
    assert "<script" not in html
    assert "**What would change this decision**" in "\n".join(brief._guidance_lines(report["items"][0], world))
