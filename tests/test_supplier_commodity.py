"""Supplier & Commodity Analyst (D3): claim parsing, registration, the 3-driver demo plan, cached evidence, Evaluator checks.
The model call is mocked; no network, no Zetaris."""

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents import evaluator, geopolitics_analyst, planner
from agents.evaluator import deterministic_checks
from agents.geopolitics_analyst import GeopoliticsAnalyst
from agents.supplier_commodity import SupplierCommodityAnalyst
from core import dashboard, demo, news
from core.calculator import FORECAST, SCENARIO
from core.ganga_data import Target
from core.news import Article
from core.shared_state import RunWorld
from core.trace import Trace

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)


def tgt(pid, name, cat, country, spend, currency="USD"):
    return Target(pid, name, cat, country, "Sup", "2026-Q3", spend, currency)


TARGETS = [tgt("P001", "GangaAccel GA100 GPU Chip", "GPU chips", "Taiwan", 1344975.0),
           tgt("P006", "GangaCool Direct-to-Chip Loop", "liquid cooling units", "Germany", 469237.5, "EUR"),
           tgt("P002", "GangaHBM-8H Memory Stack", "HBM memory", "South Korea", 330120.0),
           tgt("P003", "GangaCore ABF Substrate", "chip substrates", "Japan", 66975.0)]
KOREA = TARGETS[2]
CATALOG = {"P002": {"part_name": KOREA.part_name, "country": "South Korea"}}


def art(n, publisher, title, summary="", published="2026-10-23T10:00:00+00:00"):
    return Article(title=title, publisher=publisher, url=f"https://example.test/{n}", published=published, summary=summary)


ARTICLES = [art(1, "Reuters", "SK Hynix and Samsung raise HBM contract prices as AI demand tightens supply", "HBM prices up 8-10% next quarter"),
            art(2, "Bloomberg", "Korean memory makers sold out of HBM capacity through 2027"),
            art(3, "Financial Times", "DRAM shortage deepens as Korean suppliers shift output to HBM")]


@pytest.fixture
def analyst(tmp_path, monkeypatch):
    trace = Trace(log_dir=tmp_path, quiet=True)
    world = RunWorld("t", path=tmp_path / "world.json")
    monkeypatch.setattr(geopolitics_analyst, "fetch_news", lambda q, **kw: (ARTICLES, {"from_cache": True, "fetched_at": "2026-10-24", "errors": []}))
    return SupplierCommodityAnalyst(trace, world, {}, model="m")


def reply(analyst, monkeypatch, data, seen=None):
    def fake(system, user, step, retry_count):
        if seen is not None:
            seen.update(system=system, user=user)
        return data, type("R", (), {"model": "m"})()
    monkeypatch.setattr(analyst, "_ask_json", fake)


# ---- claim parsing ---------------------------------------------------------------------------------
def test_claim_is_parsed_into_the_shared_claim_format(analyst, monkeypatch):
    reply(analyst, monkeypatch, {"claims": [{
        "driver": "HBM contract price rise", "claim": "HBM contract prices are rising.", "part_id": "P002", "supplier_country": "South Korea",
        "direction": "cost_up", "source_ids": ["A1", "A2"], "stated_pct": None, "reasoning": "Two publishers report it."}]})
    claim = analyst.draft(KOREA, "D3")
    assert (claim.analyst, claim.part_id, claim.supplier_country, claim.direction) == ("supplier_commodity", "P002", "South Korea", "cost_up")
    assert claim.driver == "HBM contract price rise"
    assert len(claim.source_urls) == 2 and all(u.startswith("https://example.test/") for u in claim.source_urls)
    assert claim.pct_label == SCENARIO  # no article stated a %, so the scenario assumption is applied and labelled
    assert 0 < claim.pct_low <= claim.pct_high
    assert [e["publisher"] for e in claim.evidence] and all(e["excerpt"] for e in claim.evidence)
    assert analyst.world.get_claim(analyst.world.latest_claim_key("D3")).analyst == "supplier_commodity"


def test_percentage_is_forecast_only_when_a_cited_article_states_it(analyst, monkeypatch):
    reply(analyst, monkeypatch, {"claims": [{"driver": "d", "claim": "c", "part_id": "P002", "supplier_country": "South Korea", "direction": "cost_up",
                                             "source_ids": ["A1", "A2"], "stated_pct": {"low": 8, "high": 10, "source_id": "A1"}}]})
    claim = analyst.draft(KOREA, "D3")
    assert (claim.pct_label, claim.pct_low, claim.pct_high) == (FORECAST, 8, 10)


def test_invented_source_id_is_kept_so_the_evaluator_catches_it(analyst, monkeypatch):
    reply(analyst, monkeypatch, {"claims": [{"driver": "d", "claim": "c", "part_id": "P002", "supplier_country": "South Korea", "direction": "cost_up",
                                             "source_ids": ["A1", "A99"]}]})
    claim = analyst.draft(KOREA, "D3")
    assert "A99" in claim.source_urls
    assert {f.code for f in deterministic_checks(claim, {a.url: a for a in ARTICLES}, CATALOG, now=NOW, max_age_days=30)} >= {"UNKNOWN_URL"}


def test_no_claim_when_the_model_finds_no_development(analyst, monkeypatch):
    reply(analyst, monkeypatch, {"claims": []})
    assert analyst.draft(KOREA, "D3") is None


def test_prompt_is_the_memory_scope_and_never_carries_the_demo_instructions(analyst, monkeypatch):
    seen = {}
    reply(analyst, monkeypatch, {"claims": []}, seen)
    analyst.draft(KOREA, "D3")
    assert "Supplier & Commodity Analyst" in seen["system"] and "HBM" in seen["user"]
    assert "DEMO MODE" not in seen["user"]  # --demo-weak-claim stays on the geopolitics driver only


def test_geopolitics_analyst_is_unchanged_by_the_subclassing(tmp_path, monkeypatch):
    trace, world = Trace(log_dir=tmp_path, quiet=True), RunWorld("g", path=tmp_path / "w.json")
    monkeypatch.setattr(geopolitics_analyst, "fetch_news", lambda q, **kw: (ARTICLES, {"from_cache": True, "fetched_at": "x", "errors": []}))
    geo, seen = GeopoliticsAnalyst(trace, world, {}, model="m", demo_weak_claim=True), {}
    reply(geo, monkeypatch, {"claims": []}, seen)
    geo.draft(TARGETS[0], "D1")
    assert "GEOPOLITICS" not in seen["system"] and "Geopolitics Analyst" in seen["system"]
    assert geopolitics_analyst.DEMO_WEAK_CLAIM_INSTRUCTION in seen["user"]
    assert GeopoliticsAnalyst._query(TARGETS[0]) == "Taiwan GPU chips export controls OR sanctions OR tariffs OR tensions"


# ---- registration ----------------------------------------------------------------------------------
def test_planner_knows_the_analyst_and_keeps_a_reason_per_delegation():
    assert "supplier_commodity" in planner.ANALYSTS
    plan = planner.Planner._validate({"delegations": [{"analyst": "supplier_commodity", "part_id": "P002", "reason": "12.9% of spend, HBM price news"},
                                                      {"analyst": "geopolitics", "part_id": "P001", "reason": "largest spend"}],
                                      "skipped": [{"analyst": "currency", "part_id": "P003", "reason": "low spend"}]}, TARGETS)
    assert [(d["analyst"], d["part_id"]) for d in plan["delegations"]] == [("supplier_commodity", "P002"), ("geopolitics", "P001")]
    assert all(d["reason"] for d in plan["delegations"]) and plan["skipped"][0]["reason"]


def test_main_registers_the_analyst_without_demo_flags():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    m = re.search(r'"supplier_commodity": SupplierCommodityAnalyst\(([^)]*)\)', src)
    assert m and "demo_weak_claim" not in m.group(1) and "demo_untrusted_source" not in m.group(1)


def test_every_analyst_has_an_evaluator_scope():
    assert set(planner.ANALYSTS) == set(evaluator.SCOPE)


# ---- the demo plan ---------------------------------------------------------------------------------
def test_demo_plan_has_d1_d2_d3_and_d1_d2_are_unchanged():
    plan = demo.demo_plan(TARGETS, supported_currencies={"EUR", "USD", "JPY"})
    assert [(d["work_id"], d["analyst"], d["part_id"]) for d in plan["delegations"]] == [
        ("D1", "geopolitics", "P001"), ("D2", "currency", "P006"), ("D3", "supplier_commodity", "P002")]
    assert all(d["reason"] for d in plan["delegations"]) and {s["part_id"] for s in plan["skipped"]} == {"P003"}


def test_demo_plan_without_a_memory_part_stays_two_drivers():
    plan = demo.demo_plan([t for t in TARGETS if t.part_id != "P002"], supported_currencies={"EUR"})
    assert [d["work_id"] for d in plan["delegations"]] == ["D1", "D2"]


def test_demo_evidence_queries_include_the_new_analyst_both_normal_and_broadened():
    queries = [q for q, _ in demo.evidence_queries(TARGETS, supported_currencies={"EUR", "USD"})]
    assert SupplierCommodityAnalyst._query(KOREA, False) in queries and SupplierCommodityAnalyst._query(KOREA, True) in queries
    assert len(queries) == 6


# ---- cached evidence -------------------------------------------------------------------------------
def test_saved_demo_evidence_loads_for_both_queries(monkeypatch):
    monkeypatch.setattr(news, "DEMO_DIR", ROOT / "data" / "demo_evidence")
    news.use_demo_evidence(True)
    try:
        for broaden in (False, True):
            articles, info = news.fetch_news(SupplierCommodityAnalyst._query(KOREA, broaden))
            assert articles and info["from_cache"] and info["fetched_at"].startswith("2026-")
    finally:
        news.use_demo_evidence(False)


# ---- Evaluator checks apply ------------------------------------------------------------------------
def make_claim(analyst, monkeypatch, **raw):
    base = {"driver": "d", "claim": "HBM prices are rising.", "part_id": "P002", "supplier_country": "South Korea", "direction": "cost_up",
            "source_ids": ["A1", "A2"]}
    reply(analyst, monkeypatch, {"claims": [{**base, **raw}]})
    return analyst.draft(KOREA, "D3")


def codes(claim):
    return {f.code for f in deterministic_checks(claim, {a.url: a for a in ARTICLES}, CATALOG, now=NOW, max_age_days=30)}


def test_good_claim_passes_deterministic_checks(analyst, monkeypatch):
    assert codes(make_claim(analyst, monkeypatch)) - {"NO_TRUSTED_SOURCE"} == set()


def test_evaluator_sends_back_one_source_wrong_country_and_unsourced_number(analyst, monkeypatch):
    assert "TOO_FEW_SOURCES" in codes(make_claim(analyst, monkeypatch, source_ids=["A1"]))
    assert "COUNTRY_MISMATCH" in codes(make_claim(analyst, monkeypatch, supplier_country="Japan"))
    assert "NUMBER_NOT_IN_SOURCE" in codes(make_claim(analyst, monkeypatch, claim="HBM prices will rise 37% next quarter."))


def test_a_percentage_printed_by_a_cited_article_is_allowed(analyst, monkeypatch):
    assert "NUMBER_NOT_IN_SOURCE" not in codes(make_claim(analyst, monkeypatch, claim="HBM prices are up 8-10% next quarter.", source_ids=["A1", "A2", "A3"]))


# ---- 3 cards across ---------------------------------------------------------------------------------
def test_dashboard_cards_fit_three_across_on_wide_screens():
    min_px = int(re.search(r"\.cards\{[^}]*minmax\(min\(100%,(\d+)px\),1fr\)", dashboard.CSS).group(1))
    assert 3 * min_px + 2 * 20 <= 1280


# ---- regression: D3 traces of 2026-10-24 (run_20261024_205745 / run_20261024_203148) ----------------------------
# The fixture holds the verbatim 400-character prefixes the trace kept, completed to valid JSON (see its _note).
import json as _json

from agents import analyst_base
from agents.supplier_commodity import claims_from_reply
from core import llm

TRACE = _json.loads((ROOT / "tests" / "data" / "d3_trace_replies.json").read_text(encoding="utf-8"))


def model_says(analyst, monkeypatch, text, seen=None):
    """Mock only the network call: the reply text goes through the real _ask_json and the analyst's own parser."""
    def chat(messages, **kw):
        if seen is not None:
            seen["user"] = messages[-1]["content"]
        return type("R", (), {"text": text, "model": "m"})()
    monkeypatch.setattr(analyst_base.llm, "chat", chat)


def test_the_trace_prefixes_are_the_real_logged_replies():
    assert TRACE["run_205745_a11_a12_trace_prefix"] == TRACE["run_203148_attempt1_trace_prefix"]
    assert TRACE["run_205745_a11_a12_reply"].startswith(TRACE["run_205745_a11_a12_trace_prefix"][:-3])


def test_d3_prompt_is_the_memory_format_not_the_geopolitics_one(analyst, monkeypatch):
    seen = {}
    model_says(analyst, monkeypatch, '{"claims": []}', seen)
    analyst.draft(KOREA, "D3")
    assert "HBM/DRAM contract or spot prices" in seen["user"] and "never cite an id that is not in the list" in seen["user"]
    assert "geopolitical or\ntrade-policy development" not in seen["user"]  # that rule made D3 answer 'state subsidy'


def test_d1_still_gets_the_geopolitics_format(tmp_path, monkeypatch):
    trace, world, seen = Trace(log_dir=tmp_path, quiet=True), RunWorld("g", path=tmp_path / "w.json"), {}
    monkeypatch.setattr(geopolitics_analyst, "fetch_news", lambda q, **kw: (ARTICLES, {"from_cache": True, "fetched_at": "x", "errors": []}))
    geo = GeopoliticsAnalyst(trace, world, {}, model="m")
    model_says(geo, monkeypatch, '{"claims": []}', seen)
    geo.draft(TARGETS[0], "D1")
    assert "geopolitical or\ntrade-policy development" in seen["user"] and "HBM/DRAM contract" not in seen["user"]


def test_real_reply_followed_by_an_echoed_empty_claims_object_is_not_discarded(analyst, monkeypatch):
    model_says(analyst, monkeypatch, TRACE["run_205745_a11_a12_reply"] + '\nIf nothing fits I would reply {"claims": []}.')
    claim = analyst.draft(KOREA, "D3")
    assert claim is not None and claim.driver == "South Korea state subsidy"


def test_real_reply_with_a_broken_outer_wrapper_keeps_the_claim(analyst, monkeypatch):
    model_says(analyst, monkeypatch, TRACE["run_203148_revise_a3_a4_reply"][:-1])  # outer closing brace lost
    claim = analyst.draft(KOREA, "D3")
    assert claim is not None and claim.direction == "cost_down"


def test_bare_claim_object_without_the_claims_wrapper_is_accepted():
    inner = _json.loads(TRACE["run_203148_revise_a3_a4_reply"])["claims"][0]
    assert claims_from_reply("Answer: " + _json.dumps(inner)) == {"claims": [inner]}


def test_d3_parser_still_raises_on_text_without_a_claim():
    with pytest.raises(ValueError):
        claims_from_reply("I could not find anything.")


def test_d1_parser_is_unchanged_it_still_takes_the_last_json_object():
    text = TRACE["run_205745_a11_a12_reply"] + ' then {"claims": []}'
    assert GeopoliticsAnalyst._parse_reply(text) == {"claims": []} == llm.extract_json(text)


def test_invented_ids_from_the_trace_are_still_rejected_by_the_evaluator(analyst, monkeypatch):
    model_says(analyst, monkeypatch, TRACE["run_205745_a11_a12_reply"])
    claim = analyst.draft(KOREA, "D3")  # three articles shown (A1-A3), the model cited A11 and A12
    assert claim.source_urls == ["A11", "A12"]
    codes = {f.code for f in deterministic_checks(claim, {a.url: a for a in ARTICLES}, CATALOG, now=NOW, max_age_days=30)}
    assert "UNKNOWN_URL" in codes


def test_shown_article_ids_resolve_to_the_urls_the_evaluator_checks(analyst, monkeypatch):
    model_says(analyst, monkeypatch, TRACE["run_203148_revise_a3_a4_reply"].replace('"A3", "A4"', '"A1", "A3"'))
    claim = analyst.draft(KOREA, "D3")
    assert set(claim.source_urls) <= {a.url for a in ARTICLES}  # same A<n> numbering for the prompt, the claim and the pool
