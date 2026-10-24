"""--demo mode: bounded AI calls, cached dated evidence, the fixed plan, "Could not verify", and the evidence trail page. No network."""

import json
import time
import types

import openai
import pytest

from agents.evaluator import Finding, check_results
from core import demo, llm, news, unverified
from core.claims import Claim, Verdict
from core.evidence_page import build_evidence_page
from core.ganga_data import Target
from core.news import Article
from core.shared_state import RunWorld


def target(pid, country, spend):
    return Target(pid, f"Part {pid}", "cat", country, "Sup", "2026-Q3", spend)


TARGETS = [target("P001", "Taiwan", 1000.0), target("P002", "South Korea", 500.0), target("P006", "Germany", 400.0), target("P003", "Japan", 100.0)]


@pytest.fixture(autouse=True)
def reset_limits():
    yield
    llm.set_limits()
    news.use_demo_evidence(False)


# ---- the fixed demo plan -------------------------------------------------------------------------
def test_demo_plan_is_top_spend_plus_a_currency_with_fx_data():
    plan = demo.demo_plan(TARGETS, supported_currencies={"EUR", "JPY"})
    assert [(d["work_id"], d["analyst"], d["part_id"]) for d in plan["delegations"]] == [("D1", "geopolitics", "P001"), ("D2", "currency", "P006")]
    assert {s["part_id"] for s in plan["skipped"]} == {"P002", "P003"}
    assert plan["demo"] and "Planner model was not called" in plan["summary"]


def test_demo_plan_without_any_fx_part_runs_one_driver():
    assert len(demo.demo_plan(TARGETS, supported_currencies=set())["delegations"]) == 1


# ---- AI calls are bounded ------------------------------------------------------------------------
def test_run_budget_stops_new_model_calls(monkeypatch):
    llm.set_limits(budget_seconds=0.001)
    boom = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(
        create=lambda **kw: pytest.fail("no call may start after the budget is spent"))))
    monkeypatch.setattr(llm, "_client", lambda timeout=None: boom)
    time.sleep(0.1)
    with pytest.raises(llm.RunBudgetExceeded):
        llm.chat([{"role": "user", "content": "x"}], model="m")
    assert issubclass(llm.RunBudgetExceeded, openai.OpenAIError)  # so main.py reports it like any outage


def test_attempts_and_timeout_are_capped(monkeypatch):
    seen = {"calls": 0, "timeout": None}

    class Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    seen["calls"] += 1
                    raise openai.APIConnectionError(request=types.SimpleNamespace())

    def fake_client(timeout=None):
        seen["timeout"] = timeout
        return Boom
    monkeypatch.setattr(llm, "_client", fake_client)
    llm.set_limits(timeout=60, max_attempts=3, retry_wait=0)
    with pytest.raises(openai.APIConnectionError):
        llm.chat([{"role": "user", "content": "x"}], model="m", timeout=500, max_attempts=9)
    assert seen == {"calls": 3, "timeout": 60}


# ---- cached evidence -----------------------------------------------------------------------------
def test_demo_evidence_is_read_from_the_dated_cache_only(tmp_path, monkeypatch):
    monkeypatch.setattr(news, "DEMO_DIR", tmp_path)
    monkeypatch.setattr(news, "_fetch_live", lambda *a, **k: pytest.fail("demo mode must never touch the network"))
    art = Article("Old headline", "Reuters", "https://x.test/1", "2020-01-01T00:00:00+00:00")  # far older than 30 days: still replayed
    (tmp_path / news._cache_path("q one").name).write_text(json.dumps(
        {"query": "q one", "fetched_at": "2026-10-24T01:00:00+00:00", "articles": [art.to_dict()]}), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"cached_on": "2026-10-24"}), encoding="utf-8")
    news.use_demo_evidence(True)
    got, info = news.fetch_news("q one")
    assert [a.url for a in got] == ["https://x.test/1"] and info["from_cache"]
    missing, info = news.fetch_news("never saved")
    assert missing == [] and info["errors"]
    assert news.demo_evidence_date() == "2026-10-24"


def test_shipped_demo_evidence_covers_every_demo_query():
    manifest = news.DEMO_DIR / "manifest.json"
    if not manifest.exists():
        pytest.skip("run scripts/save_demo_evidence.py first")
    for q in json.loads(manifest.read_text(encoding="utf-8"))["queries"]:
        assert q["articles"] > 0 and (news.DEMO_DIR / q["file"]).exists()


# ---- check results and "Could not verify" --------------------------------------------------------
def test_check_results_name_every_group_and_flag_failures():
    rows = check_results([Finding("TOO_FEW_SOURCES", "Only 1 source cited")])
    failed = [r for r in rows if not r["ok"]]
    assert len(rows) >= 6 and len(failed) == 1 and "TOO_FEW_SOURCES" in failed[0]["detail"]
    assert all(r["ok"] for r in check_results([]))


def _item(status, work_id="D1", **d):
    return {"delegation": {"work_id": work_id, "analyst": "geopolitics", "part_id": "P001", "action": "run", "status": status, **d},
            "attempts": [], "impact": None}


def test_failed_and_unevidenced_drivers_are_could_not_verify():
    report = {"items": [_item("NOT_ASSESSED", "D1", error="APIConnectionError"), _item("UNVERIFIED", "D2", note="withdrawn"),
                        _item("NO_CLAIM", "D3", note="no recent articles found"), _item("PASS", "D4")]}
    got = unverified.collect(report)
    assert [u["status"] for u in got] == ["NOT_ASSESSED", "UNVERIFIED", "NO_CLAIM"]
    assert "APIConnectionError" in got[0]["reason"] and "missing evidence" in got[2]["reason"]


def test_a_real_no_material_move_is_not_called_unverified():
    report = {"items": [_item("NO_CLAIM", note="GBP/EUR moved +0.2% (30d): below the 1% materiality threshold")]}
    assert unverified.collect(report) == [] and len(unverified.no_signal(report)) == 1


# ---- the evidence trail page ---------------------------------------------------------------------
def test_evidence_page_shows_the_whole_chain_and_never_zero_for_unverified(tmp_path):
    world = RunWorld("t1", path=tmp_path / "w.json")
    world.start_run({"run_id": "t1"})
    arts = [Article("Taiwan curbs chip exports", "Reuters", "https://x.test/a", "2026-10-20T00:00:00+00:00"),
            Article("Taipei tightens AI chip rules", "Bloomberg", "https://x.test/b", "2026-10-21T00:00:00+00:00")]
    world.record_articles(arts, "Part P001", "Taiwan", "geopolitics")
    world.record_plan({"summary": "s", "delegations": [
        {"analyst": "geopolitics", "part_id": "P001", "work_id": "D1", "reason": "biggest spend"},
        {"analyst": "currency", "part_id": "P006", "work_id": "D2", "reason": "fx"}], "skipped": []}, [])
    c1 = Claim("D1", "Curbs raise GPU costs.", "P001", "Part P001", "Taiwan", "cost_up", 3, 8, ["https://x.test/a"], "r", 1, "geopolitics", driver="Export curbs")
    k1 = world.write_claim(c1, "D1", "geopolitics")
    world.write_verdict(k1, Verdict("SEND_BACK", ["TOO_FEW_SOURCES: Only 1 source cited"], 1,
                                    checks=check_results([Finding("TOO_FEW_SOURCES", "Only 1 source cited")])), 0)
    c2 = Claim("D1", "Curbs raise GPU costs.", "P001", "Part P001", "Taiwan", "cost_up", 3, 8, [a.url for a in arts], "r", 2, "geopolitics", driver="Export curbs")
    k2 = world.write_claim(c2, "D1", "geopolitics")
    world.write_verdict(k2, Verdict("PASS", ["supported"], 2, "strong", True, check_results([])), 1)
    world.write_final("D1", "PASS", 1)
    world.write_error("D2", "APIConnectionError: down")
    html = build_evidence_page(world.report(), world, {"weak": True}, {"llm_ok": 3, "llm_fail": 1},
                               evidence_date="2026-10-24", trace_file="logs/x.jsonl")
    for needle in ("News is cached and dated for reproducibility (cached 2026-10-24)", "Reuters", "Bloomberg", "Tier 1", "SENT BACK", "TOO_FEW_SOURCES", "Deterministic checks",
                   "Could not verify", "APIConnectionError", "NOT zero impact"):
        assert needle in html, needle
    assert "<script" not in html


# ---- the world-state file is written once per ingest, not once per event ------------------------------
def test_ingest_writes_the_world_file_once(tmp_path, monkeypatch):
    from engines.world_model import store as store_mod
    writes = []
    original = store_mod.EventStore._write
    monkeypatch.setattr(store_mod.EventStore, "_write", lambda self: (writes.append(1), original(self))[1])
    world = RunWorld("w1", path=tmp_path / "w.json")
    arts = [Article(f"Headline {i}", "Reuters", f"https://x.test/{i}", "2026-10-20T00:00:00+00:00") for i in range(30)]
    before = len(writes)
    world.record_articles(arts, "Part", "Taiwan", "geopolitics")
    assert len(writes) - before == 1
    assert len(RunWorld("w1", path=tmp_path / "w.json").world.events()) == len(world.world.events())  # and the file replays completely
