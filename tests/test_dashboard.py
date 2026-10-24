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

pytestmark = pytest.mark.skipif(not (V3 / "world_state_b25b249f.json").exists() or not (LIVE / "world_state_5b82b132.json").exists(),
                                reason="saved sample runs not present")


@pytest.fixture(scope="module")
def runs():
    r = dashboard.default_runs(BASE)
    assert [x["run_id"] for x in r] == ["b25b249f", "5b82b132"]
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
        world_file = next((V3 if r["run_id"] == "b25b249f" else LIVE).glob("world_state_*.json"))
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
    card = re.search(r'<div class="card"[^>]*data-chain="[^"]*-D1">(.*?)</div><div class="hint">', sec, re.S).group(1)
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
                 "Final decision", "What would change this decision", "TOO_FEW_SOURCES", "calculated in Python, never by the AI"):
        assert text in chain, text
    assert chain.index("Sent back") < chain.index("Revised claim") < chain.index("GBP calculation") < chain.index("Final decision")
    assert "Sent back 1x" in v3 and "Red Team changed it" in v3


def test_proof_chips_read_from_files(runs, page):
    live = _section(page, runs[1])
    r = runs[1]["report"]["run"]
    assert f"{r['llm_ok']} model calls ok" in live and "nvidia/nemotron" in live
    assert "0 GBP numbers written by AI" in live
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
    assert out.exists() and out.read_text(encoding="utf-8").count('<div class="run"') == 2
