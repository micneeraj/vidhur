"""The CFO dashboard is built only from saved run files: every GBP figure must equal the run files, a driver that could not be verified shows
no GBP figure, and the page holds no secrets. No model call, no service."""

import os
import re
from html import unescape
from pathlib import Path

import pytest

from core import dashboard
from core.evidence_page import g

BASE = Path(__file__).resolve().parents[1] / "output_examples"
V3 = BASE / "demo_run_20261024_v3"
LIVE = BASE / "live_run_20261024_could_not_verify"
THREE = BASE / "live_run_20261024_3drivers"
FOLDERS = {"b25b249f": V3, "d9decb7d": THREE, "5b82b132": LIVE}

pytestmark = pytest.mark.skipif(not (V3 / "world_state_b25b249f.json").exists() or not (LIVE / "world_state_5b82b132.json").exists()
                                or not (THREE / "world_state_d9decb7d.json").exists(),
                                reason="saved sample runs not present")


@pytest.fixture(scope="module")
def runs():
    r = dashboard.default_runs(BASE)
    assert [x["run_id"] for x in r] == ["b25b249f", "d9decb7d", "5b82b132"]  # switcher order
    return r


@pytest.fixture(scope="module")
def page(runs):
    return dashboard.build_dashboard_html(runs)


def _section(page, run):
    m = re.search(rf'<div class="run" id="{run["id"]}">(.*?)(?=<div class="run" id=|<footer>)', page, re.S)
    assert m, run["id"]
    return m.group(1)


def _gbp_amounts(html):
    return [int(x.replace(",", "")) for x in re.findall(r"£([\d,]+)", unescape(re.sub(r"<[^>]+>", " ", html)))]


def test_builds_both_runs_with_switcher_and_question(page, runs):
    assert page.startswith("<!doctype html>")
    for r in runs:
        assert f'data-run="{r["id"]}"' in page and r["label"] in page
    assert dashboard.QUESTION in page
    assert "Built by a commercial auditor" in page and "OBSERVED" in page and "FORECAST" in page and "SCENARIO" in page
    assert "https://" not in re.sub(r'href="https://[^"]*"', "", page)  # no CDN or external asset: only article links


def test_no_external_assets(page):
    assert "<link" not in page and not re.search(r"<script[^>]+src=", page)


def test_every_verified_gbp_value_equals_the_run_files(runs, page):
    for r in runs:
        sec = _section(page, r)
        for item in r["report"]["items"]:
            if item["delegation"].get("status") == "PASS" and item["impact"]:
                m = item["impact"]
                for k in ("worst_gbp", "likely_gbp", "best_gbp"):
                    assert g(m[k]) in sec, (r["run_id"], k)


def test_every_pound_figure_comes_from_the_saved_files(runs, page):
    for r in runs:
        world_file = next(FOLDERS[r["run_id"]].glob("world_state_*.json"))
        text = world_file.read_text(encoding="utf-8")
        allowed = {int(x.replace(",", "")) for x in re.findall(r"£([\d,]+)", text)}
        allowed |= {round(float(x)) for x in re.findall(r'"(?:worst_gbp|likely_gbp|best_gbp|baseline_gbp|baseline_quarterly_spend_gbp|worst|likely|best)": (-?[\d.]+)', text)}
        allowed |= {abs(round(float(x))) for x in re.findall(r'"(?:worst_gbp|likely_gbp|best_gbp)": (-?[\d.]+)', text)}
        ver = [i["impact"] for i in r["report"]["items"] if i["impact"]]
        for k in ("worst_gbp", "likely_gbp", "best_gbp"):
            allowed.add(abs(round(sum(m[k] for m in ver))))
        stray = [a for a in _gbp_amounts(_section(page, r)) if a not in allowed]
        assert not stray, (r["run_id"], stray)


def test_could_not_verify_driver_shows_no_gbp_figure(runs, page):
    live = next(r for r in runs if r["run_id"] == "5b82b132")
    cnv = [i for i, ok in dashboard._drivers(live["report"]) if not ok]
    assert [i["delegation"]["work_id"] for i in cnv] == ["D1"]
    sec = _section(page, live)
    card = re.search(r'<div class="card[^"]*"[^>]*data-chain="[^"]*-D1">(.*?)</div><div class="hint">', sec, re.S).group(1)
    assert "COULD NOT VERIFY" in card and "NOT zero impact" in card
    assert "£" not in card and "likely" not in card
    chain = re.search(r'<section class="chain[^"]*" id="[^"]*-chain-D1">(.*?)</section>', sec, re.S).group(1)
    assert "£" not in chain
    assert "Chain stopped here" in chain and "TOO_FEW_SOURCES" in chain and "find two independent sources" in chain
    assert "taiwan" in unescape(sec).lower() and "could not verify, no GBP figure (not zero impact)" in sec


def test_total_in_the_answer_is_the_sum_of_verified_drivers(runs, page):
    for r in runs:
        ver = [i["impact"] for i in r["report"]["items"] if i["delegation"].get("status") == "PASS" and i["impact"]]
        assert f"likely {g(sum(m['likely_gbp'] for m in ver))}" in _section(page, r)


def test_why_chain_steps_for_a_send_back_and_red_team_change(runs, page):
    v3 = _section(page, runs[0])
    chain = re.search(r'<section class="chain[^"]*" id="[^"]*-chain-D1">(.*?)</section>', v3, re.S).group(1)
    for text in ("Claim", "Sources", "Evaluator checks", "Sent back", "Revised claim", "GBP calculation", "First proposal", "Red Team objections",
                 "Final decision", "What would change this decision", "TOO_FEW_SOURCES", "calculated by Python from recorded formulas and FX rates"):
        assert text in chain, text
    assert chain.index("Sent back") < chain.index("Revised claim") < chain.index("GBP calculation") < chain.index("Final decision")
    assert "Sent back 1x" in v3 and "Red Team changed it" in v3


def test_proof_chips_read_from_files(runs, page):
    live = _section(page, runs[1])
    r = runs[1]["report"]["run"]
    assert f"{r['llm_ok']} model calls ok" in live and "nvidia/nemotron" in live
    assert "0 GBP amounts generated by AI (Python calculates all GBP)" in live
    assert "1 claim sent back" in live


def test_empty_chips_are_left_out():
    class W:
        def article(self, url):
            return None
    run = {"models": [], "world": W()}
    chips = dashboard._proof_chips(run, {"run": {}, "decisions": {}}, [])
    assert "model calls" not in chips and "run time" not in chips


def test_no_secrets_in_the_page(page):
    low = page.lower()
    for bad in ("bearer ", "nvapi-", "api_key", "authorization", "sk-"):
        assert bad not in low
    for k, v in os.environ.items():
        if any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET", "PASSWORD")) and len(v) >= 8:
            assert v not in page, k


def test_write_dashboard_is_self_contained(tmp_path, runs):
    out = dashboard.write_dashboard(runs, tmp_path / "dashboard.html")
    assert out.exists() and out.read_text(encoding="utf-8").count('<div class="run"') == 3


# ---- empty runs are never shown, and tests never register a run -------------------------------------------
def _fake_run(llm_ok, with_driver=True):
    class Fake:
        pass
    items = []
    report = {"run": {"status": "finished", "llm_ok": llm_ok, "elapsed_seconds": 0}, "items": items, "decisions": {}}
    return {"id": "run-fake", "label": "Latest run (fake)", "run_id": "fake", "world": Fake(), "report": report, "models": [], "trail": None}


def test_empty_run_with_no_drivers_or_model_calls_is_not_real(runs):
    assert dashboard.is_real(runs[0]) and dashboard.is_real(runs[1])
    assert not dashboard.is_real(_fake_run(0))
    assert not dashboard.is_real(_fake_run(5))  # model calls but no driver


def test_empty_run_is_left_out_of_the_page(runs, tmp_path):
    page = dashboard.build_dashboard_html(runs + [_fake_run(0)])
    assert "Latest run (fake)" not in page and page.count('<div class="run"') == 3
    out = dashboard.write_dashboard(runs + [_fake_run(0)], tmp_path / "d.html")
    assert "Latest run (fake)" not in out.read_text(encoding="utf-8")


def test_default_view_is_the_v3_run(page, runs):
    assert re.search(r'<button type="button" data-run="run-b25b249f" data-default="1">v3 run</button>', page)
    assert page.count("data-default") == 2  # the attribute on the v3 button, and the JS lookup
    assert re.findall(r'data-run="run-\w+"[^>]*>([^<]+)</button>', page) == ["v3 run", "3-driver run", "Live run - could not verify"]
    assert "Latest run" not in page


def test_latest_run_needs_a_finished_real_run(tmp_path):
    class W:
        run_id, path = "x", tmp_path / "missing.json"
    assert dashboard.latest_run(W(), tmp_path / "t.jsonl", {"run": {"status": "finished"}}) is None  # no saved world file
    (tmp_path / "missing.json").write_text("{}", encoding="utf-8")
    assert dashboard.latest_run(W(), tmp_path / "t.jsonl", {"run": {"status": "partial"}}) is None  # partial run is not added


def test_tests_cannot_write_the_real_dashboard():
    assert dashboard.OUT_FILE != BASE / "dashboard.html" and "dashboard_test_output" in dashboard.OUT_FILE.name


# ---- regression: run d9decb7d, D1 Red Team raised 3 objections, the Decision Maker's response timed out ----------
def test_three_driver_run_shows_the_real_objection_count_and_an_unresolved_label(runs, page):
    run = next(r for r in runs if r["run_id"] == "d9decb7d")
    assert run["report"]["decisions"]["D1"]["outcome"]["status"] == "NOT_CHALLENGED"  # how the saved (pre-fix) run recorded it
    sec = _section(page, run)
    card = re.search(r'<div class="card[^"]*"[^>]*data-chain="[^"]*-D1">(.*?)</div><div class="hint">', sec, re.S).group(1)
    assert "DECISION PENDING REVIEW" in card and "Provisional recommendation:" in card and "unreviewed" not in card
    chain = re.search(r'<section class="chain[^"]*" id="[^"]*-chain-D1">(.*?)</section>', sec, re.S).group(1)
    text = unescape(chain)
    assert "Red Team raised 3 objections (2 high, 1 medium)" in text and "response timed out, so the challenge is unresolved" in text
    assert "3 objection(s) raised" in text and "0 objection(s)" not in text and "0 round(s)" not in text
    d3 = re.search(r'<section class="chain[^"]*" id="[^"]*-chain-D3">(.*?)</section>', sec, re.S).group(1)
    assert "The Red Team did not run" in d3 and "unresolved" not in d3  # no objections raised: a true "could not run"
