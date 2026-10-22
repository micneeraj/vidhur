"""Planner validation, currency maths and consistency checks, article ranking. No network, no model calls."""

from datetime import datetime, timedelta, timezone

import pytest

from agents.evaluator import deterministic_checks
from agents.planner import MAX_DELEGATIONS, Planner
from core import fx
from core.claims import Claim
from core.ganga_data import Target
from core.news import Article, rank_articles

NOW = datetime.now(timezone.utc)


def target(part_id, country, spend):
    return Target(part_id, f"Part {part_id}", "cat", country, "Supplier", "2026-Q3", spend)


TARGETS = [target("P001", "Taiwan", 1000), target("P002", "South Korea", 500), target("P003", "Japan", 100)]


def test_planner_drops_unknown_parts_and_analysts_and_duplicates():
    data = {"summary": "s", "delegations": [
        {"analyst": "geopolitics", "part_id": "P001", "reason": "big"},
        {"analyst": "geopolitics", "part_id": "P001", "reason": "dup"},
        {"analyst": "astrology", "part_id": "P002", "reason": "bad analyst"},
        {"analyst": "currency", "part_id": "P999", "reason": "bad part"},
        {"analyst": "currency", "part_id": "P002", "reason": "won moved"}]}
    plan = Planner._validate(data, TARGETS)
    assert [(d["analyst"], d["part_id"], d["work_id"]) for d in plan["delegations"]] == [("geopolitics", "P001", "D1"), ("currency", "P002", "D2")]
    assert plan["fallback"] is False


def test_planner_caps_the_number_of_delegations():
    data = {"delegations": [{"analyst": "geopolitics", "part_id": p, "reason": "r"} for p in ("P001", "P002", "P003")]
            + [{"analyst": "currency", "part_id": p, "reason": "r"} for p in ("P001", "P002", "P003")]}
    assert len(Planner._validate(data, TARGETS)["delegations"]) == min(6, MAX_DELEGATIONS)


def test_planner_rejects_an_unusable_reply_and_the_fallback_is_flagged():
    assert Planner._validate(None, TARGETS) is None
    assert Planner._validate({"delegations": []}, TARGETS) is None
    fallback = Planner._fallback(TARGETS)
    assert fallback["fallback"] is True and "FALLBACK" in fallback["summary"] and len(fallback["delegations"]) == 3


def test_fx_pct_change_is_the_change_in_gbp_cost_of_the_currency():
    # 1 GBP bought 1900 won, now buys 1800: the won got dearer in pounds by ~5.6%
    assert fx.pct_change_in_gbp_cost(1900, 1800) == pytest.approx(5.5556, abs=1e-3)
    assert fx.pct_change_in_gbp_cost(1800, 1900) < 0
    assert fx.pct_change_in_gbp_cost(1.3, 1.3) == 0


def test_taiwan_dollar_is_known_to_be_unsupported_by_the_ecb():
    assert fx.COUNTRY_CURRENCY["Taiwan"] == "TWD"


def fx_claim(direction="cost_up", low=2.28, high=9.87, computed=None):
    computed = computed if computed is not None else {"move_30d_pct": 2.28, "move_90d_pct": 9.87}
    return Claim("D2", "Won rose", "P002", "HBM", "South Korea", direction, low, high, ["fx", "news"], "r", 1, "currency", computed)


FX_POOL = {"fx": Article("GBP/KRW", fx.PUBLISHER, "fx", NOW.isoformat(timespec="seconds")),
           "news": Article("Won strengthens", "Reuters", "news", (NOW - timedelta(days=3)).isoformat(timespec="seconds"))}
CATALOG = {"P002": {"part_name": "HBM", "country": "South Korea"}}


def codes(claim):
    return {f.code for f in deterministic_checks(claim, FX_POOL, CATALOG, now=NOW)}


def test_currency_claim_matching_the_computed_moves_passes_the_deterministic_checks():
    assert codes(fx_claim()) == set()


def test_currency_claim_that_disagrees_with_the_computed_numbers_is_sent_back():
    assert "FX_MISMATCH" in codes(fx_claim(direction="cost_down"))
    assert "FX_MISMATCH" in codes(fx_claim(low=5, high=20))
    assert "FX_NO_DATA" in codes(fx_claim(computed={}))


def test_rank_articles_prefers_relevance_then_recency():
    def art(title, days):
        return Article(title, "P", f"u-{title}", (NOW - timedelta(days=days)).isoformat(timespec="seconds"))
    arts = [art("Weather in Paris", 0), art("Taiwan chip export controls tighten", 10), art("Taiwan chip export controls tighten again", 1),
            art("Football results", 0)]
    top = rank_articles(arts, [(["taiwan"], 3), (["chip", "export"], 2)], k=2, now=NOW)
    assert [a.title for a in top] == ["Taiwan chip export controls tighten again", "Taiwan chip export controls tighten"]
