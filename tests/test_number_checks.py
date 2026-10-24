"""The deterministic number checks: NUMBER_NOT_IN_SOURCE, FX_MISMATCH, CURRENCY_COUNTRY_MISMATCH, GBP_MISMATCH, DUPLICATE_SOURCE,
STALE_SOURCE. No AI, no network (FX rates are stubbed)."""

from datetime import datetime, timezone

import pytest

from agents.evaluator import (Evaluator, claim_figures, deterministic_checks, duplicate_findings, gbp_findings, independent_groups,
                              number_findings, original_outlet)
from core.claims import Claim
from core.news import Article

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)
CATALOG = {"P001": {"part_name": "GPU", "country": "Taiwan"}, "P002": {"part_name": "HBM", "country": "South Korea"}}
CALC = {"baseline_gbp": 800000.0, "result_gbp": {"worst": 96000.0, "likely": 48000.0, "best": 16000.0}}


def art(n, publisher, title, summary="", days_old=2):
    published = datetime(2026, 10, 22 - days_old, 10, tzinfo=timezone.utc).isoformat() if days_old < 22 else datetime(2026, 8, 1, tzinfo=timezone.utc).isoformat()
    return Article(title=title, publisher=publisher, url=f"https://example.test/{n}", published=published, summary=summary)


REUTERS = art(1, "Reuters", "Taiwan tightens chip export rules", "Taipei said exporters must obtain licences; costs may rise 5% next quarter.")
BLOOMBERG = art(2, "Bloomberg", "Taipei curbs AI chip shipments", "Officials put AI accelerators on a controlled list.")
POOL = {a.url: a for a in (REUTERS, BLOOMBERG)}


def make(text="Export curbs raise GPU costs.", urls=("https://example.test/1", "https://example.test/2"), **kw):
    return Claim("D1", text, kw.pop("part_id", "P001"), "GPU", kw.pop("country", "Taiwan"), "cost_up", kw.pop("low", 2.0), kw.pop("high", 12.0),
                 list(urls), **kw)


def codes(claim, pool=POOL, calc=None, fx_rate_fn=None):
    return {f.code for f in deterministic_checks(claim, pool, CATALOG, now=NOW, max_age_days=30, calc=calc, fx_rate_fn=fx_rate_fn)}


# ---- NUMBER_NOT_IN_SOURCE -----------------------------------------------------------------------
def test_percentage_printed_by_a_cited_source_passes():
    assert "NUMBER_NOT_IN_SOURCE" not in codes(make("Costs may rise 5% next quarter."))
    assert "NUMBER_NOT_IN_SOURCE" not in codes(make("Costs may rise 5 percent."))


def test_fabricated_percentage_is_caught():
    assert "NUMBER_NOT_IN_SOURCE" in codes(make("Export curbs will raise GPU costs by 17%."))


def test_a_number_that_only_contains_the_digits_does_not_match():
    assert "NUMBER_NOT_IN_SOURCE" in codes(make("Costs may rise 15%."))  # the source says 5, not 15


def test_scenario_tagged_figure_is_exempt_and_untagged_neighbour_is_not():
    assert "NUMBER_NOT_IN_SOURCE" not in codes(make("Our scenario assumes 12% (SCENARIO)."))
    assert "NUMBER_NOT_IN_SOURCE" in codes(make("Our scenario assumes 12% (SCENARIO) and then 30%."))


def test_non_gbp_money_must_be_in_the_source_but_gbp_is_left_to_the_calculator_check():
    assert "NUMBER_NOT_IN_SOURCE" in codes(make("Prices hit $4,000 per chip."))
    assert "NUMBER_NOT_IN_SOURCE" not in codes(make("The baseline is £800,000."), calc=CALC)


def test_a_forecast_labelled_range_must_be_printed_by_a_source():
    assert "NUMBER_NOT_IN_SOURCE" in codes(make(pct_label="FORECAST", low=3, high=9))
    assert "NUMBER_NOT_IN_SOURCE" not in codes(make(pct_label="SCENARIO", low=3, high=9))


def test_a_rounded_quote_of_a_source_figure_counts():
    fx_art = art(3, "Frankfurter (ECB reference rates)", "GBP/KRW ECB reference rates: +3.52% over 90 days")
    pool = {**POOL, fx_art.url: fx_art}
    claim = make("The won rose 3.5% over 90 days.", urls=(fx_art.url, REUTERS.url))
    assert number_findings(claim, [fx_art, REUTERS]) == []
    assert codes(claim, pool) >= set()  # no crash


def test_figure_extraction():
    f = claim_figures("Up 4-9 percent, i.e. £1.2m or GBP 12,500 or $3k (SCENARIO); 2025 is not a figure; GBP/JPY neither")
    assert [p[0] for p in f["percents"]] == [9.0]
    assert f["money"] == [("GBP", 1_200_000.0, False), ("GBP", 12500.0, False), ("USD", 3000.0, True)]


# ---- FX_MISMATCH ----------------------------------------------------------------------------------
REAL = {"2026-07-24": 1900.0, "2026-10-22": 1850.0}


def real_rate(currency, day):
    return REAL[day], day


def fx_claim(**computed_overrides):
    computed = {"currency": "KRW", "move_30d_pct": 1.5, "move_90d_pct": 2.7, "start_date": "2026-07-24", "end_date": "2026-10-22",
                "start_rate": 1900.0, "end_rate": 1850.0}
    computed.update(computed_overrides)
    return make("The won moved against sterling.", part_id="P002", country="South Korea", analyst="currency", currency="KRW",
                low=1.5, high=2.7, computed=computed)


def test_correct_fx_rates_pass():
    assert codes(fx_claim(), fx_rate_fn=real_rate) == set()


def test_rate_off_by_more_than_two_percent_is_caught_and_two_percent_is_tolerated():
    assert "FX_MISMATCH" in codes(fx_claim(end_rate=1850.0 * 1.05), fx_rate_fn=real_rate)
    ok = [f for f in deterministic_checks(fx_claim(end_rate=1850.0 * 1.015), POOL, CATALOG, now=NOW, max_age_days=30, fx_rate_fn=real_rate)
          if "differs from the real ECB rate" in f.message]
    assert ok == []  # 1.5% off the real rate is inside the 2% tolerance


def test_a_move_that_does_not_follow_from_its_own_rates_is_caught():
    assert "FX_MISMATCH" in codes(fx_claim(move_90d_pct=9.0), fx_rate_fn=real_rate)


def test_a_rate_that_cannot_be_checked_is_not_accepted():
    def down(currency, day):
        raise ConnectionError("offline")

    assert "FX_MISMATCH" in codes(fx_claim(), fx_rate_fn=down)


# ---- CURRENCY_COUNTRY_MISMATCH --------------------------------------------------------------------
@pytest.mark.parametrize("country,currency,bad", [
    ("Taiwan", "TWD", False), ("Taiwan", "USD", False), ("Taiwan", "KRW", True), ("South Korea", "KRW", False), ("South Korea", "JPY", True),
    ("Germany", "EUR", False), ("Germany", "USD", True), ("China", "CNY", False), ("China", "TWD", True)])
def test_currency_must_match_supplier_country(country, currency, bad):
    assert ("CURRENCY_COUNTRY_MISMATCH" in codes(make(country=country, currency=currency), fx_rate_fn=real_rate)) is bad


def test_no_currency_means_nothing_to_check():
    assert "CURRENCY_COUNTRY_MISMATCH" not in codes(make())


# ---- GBP_MISMATCH ---------------------------------------------------------------------------------
def test_gbp_figure_matching_the_calculator_passes_within_ten_percent():
    assert gbp_findings(make("Exposure is £800,000 a quarter."), CALC) == []
    assert gbp_findings(make("Exposure is £870,000 a quarter."), CALC) == []      # +8.75%
    assert gbp_findings(make("The likely rise is GBP 48,000."), CALC) == []


def test_gbp_figure_more_than_ten_percent_off_is_caught():
    f = gbp_findings(make("Exposure is £900,000 a quarter."), CALC)  # +12.5%
    assert [x.code for x in f] == ["GBP_MISMATCH"] and "calculator" in f[0].message
    assert gbp_findings(make("Exposure is GBP 8,000,000."), CALC)


def test_gbp_figure_with_no_calculator_result_cannot_be_verified():
    assert [x.code for x in gbp_findings(make("Exposure is £800,000."), None)] == ["GBP_MISMATCH"]
    assert gbp_findings(make("No money mentioned."), None) == []


def test_evaluator_checks_gbp_against_its_calculator(tmp_path):
    from core.shared_state import RunWorld
    from core.trace import Trace

    class Impact:
        calc = CALC

    world, trace = RunWorld("t-gbp", path=tmp_path / "w.json"), Trace(log_dir=tmp_path, quiet=True)
    ev = Evaluator(CATALOG, POOL, world, trace, model="m", now=NOW, calc_fn=lambda c: Impact)
    bad = make("Exposure is £8,000,000 a quarter.")
    key = world.write_claim(bad, "D1", "t")
    verdict = ev.evaluate(key)
    assert verdict.verdict == "SEND_BACK" and any(r.startswith("GBP_MISMATCH") for r in verdict.reasons) and not verdict.llm_checked


# ---- DUPLICATE_SOURCE -----------------------------------------------------------------------------
def test_same_outlet_counts_once():
    a, b = art(1, "Reuters", "Story one", "x"), art(2, "reuters.com", "Another headline entirely", "y")
    assert len(independent_groups([a, b])) == 1 and duplicate_findings([a, b])[0].code == "DUPLICATE_SOURCE"


def test_near_identical_excerpt_from_different_outlets_counts_once():
    text = "Taiwan's economy ministry said exporters of advanced AI accelerators must now obtain licences before shipping abroad."
    a, b = art(1, "Reuters", "Headline A", text), art(2, "The Guardian", "A quite different headline B", text + " Analysts agree.")
    f = duplicate_findings([a, b])
    assert f and "near-identical excerpt" in f[0].message


def test_same_original_outlet_counts_once():
    a = art(1, "Reuters", "Chip curbs", "Taiwan tightens export rules for AI chips.")
    b = art(2, "Yahoo News", "Chip curbs bite", "Exporters face new licences, via Reuters. Shipments could slow.")
    assert original_outlet(b) == "reuters" and len(independent_groups([a, b])) == 1


def test_genuinely_different_sources_stay_independent():
    assert duplicate_findings([REUTERS, BLOOMBERG]) == [] and len(independent_groups([REUTERS, BLOOMBERG])) == 2


def test_duplicates_inside_a_larger_set_leave_enough_independent_sources():
    twin = art(3, "Reuters", "Taiwan tightens chip export rules (update)", "More.")
    assert duplicate_findings([REUTERS, twin, BLOOMBERG]) == []


def test_claim_citing_duplicates_is_sent_back():
    twin = art(3, "Reuters", "Taiwan chip makers brace", "More.")
    assert "DUPLICATE_SOURCE" in codes(make(urls=(REUTERS.url, twin.url)), pool={**POOL, twin.url: twin})


# ---- STALE_SOURCE ---------------------------------------------------------------------------------
def test_source_older_than_the_configured_limit_is_stale():
    old = art(9, "Reuters", "Old story", "Old.", days_old=40)
    pool = {**POOL, old.url: old}
    assert "STALE_SOURCE" in codes(make(urls=(BLOOMBERG.url, old.url)), pool=pool)
    assert "STALE_SOURCE" not in codes(make(urls=(REUTERS.url, BLOOMBERG.url)))


def test_freshness_limit_comes_from_config(monkeypatch):
    monkeypatch.setenv("EVIDENCE_MAX_AGE_DAYS", "1")
    assert {f.code for f in deterministic_checks(make(), POOL, CATALOG, now=NOW)} >= {"STALE_SOURCE"}
