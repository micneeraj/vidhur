"""Unit tests for scripts/eval_planted_claims.py, scripts/eval_backtest.py and core/asof.py. No network, no Zetaris, no model calls."""

import importlib.util
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agents.evaluator import deterministic_checks
from core import asof
from core.claims import Verdict
from core.evalutil import llm_summary
from core.fx import FXMove

ROOT = asof.config.ROOT


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


planted = load("eval_planted_claims")
backtest = load("eval_backtest")

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)
CATALOG = {"P001": {"part_name": "GPU", "country": "Taiwan"}, "P002": {"part_name": "HBM", "country": "South Korea"},
           "P003": {"part_name": "ABF", "country": "Japan"}, "P004": {"part_name": "PCB", "country": "China"},
           "P007": {"part_name": "GaN", "country": "China"}}
MOVE = FXMove("KRW", "2026-07-24", "2026-10-22", 1900.0, 1850.0, 1.5, 2.7, "2026-09-22", "2026-07-24", "https://api.frankfurter.dev/x")


# ------------------------------------------------------------------ planted claims
def planted_rows():
    return planted.read_csv(planted.CLAIMS_CSV)


def build(row):
    pool = planted.build_pool(planted.read_csv(planted.SOURCES_CSV), NOW)
    fx_art = SimpleNamespace(url=MOVE.url)
    pool_with_fx = dict(pool)
    from core.fx import fx_source_article
    art = fx_source_article(MOVE)
    pool_with_fx[art.url] = art
    jpy = FXMove("JPY", "2026-07-24", "2026-10-22", 1900.0, 1850.0, 1.5, 2.7, "2026-09-22", "2026-07-24", MOVE.url)
    claim = planted.build_claim(row, pool, {"KRW": art, "JPY": art}, {"KRW": MOVE, "JPY": jpy},
                                {"P001": 1_000_000.0, "P002": 300_000.0}, names={"P001": "GPU"})
    return claim, pool_with_fx


def test_planted_dataset_has_20_false_and_5_controls():
    rows = planted_rows()
    assert sum(r["group"] == "false" for r in rows) == 20
    assert sum(r["group"] == "control" for r in rows) == 5
    assert len({r["case_id"] for r in rows}) == 25
    assert all(r["what_is_wrong"] for r in rows)
    assert {r["category"] for r in rows if r["group"] == "false"} >= {
        "wrong_fx_number", "wrong_currency_pair", "stale_source", "untrusted_publisher_only", "single_source", "copied_report",
        "wrong_supplier_country", "gbp_impact_mismatch", "cause_not_supported", "wrong_date"}


def test_every_cited_source_exists():
    ids = {r["source_id"] for r in planted.read_csv(planted.SOURCES_CSV)}
    for r in planted_rows():
        for sid in r["sources"].split(";"):
            assert sid == "FX" or sid in ids, (r["case_id"], sid)


def test_fill_gbp_uses_real_spend():
    assert planted.fill_gbp("bill of {gbp:P001:10} a quarter", {"P001": 1_000_000.0}) == "bill of GBP 10,000,000 a quarter"


def test_fx_modes():
    d, lo, hi, comp = planted.apply_fx_mode("real", MOVE)
    assert (d, lo, hi) == ("cost_up", 1.5, 2.7) and comp["move_90d_pct"] == 2.7
    assert planted.apply_fx_mode("bad_direction", MOVE)[0] == "cost_down"
    assert planted.apply_fx_mode("bad_range", MOVE)[2] > 2.7
    assert planted.apply_fx_mode("falsified", MOVE)[3]["move_90d_pct"] == 9.0


RATES = {"2026-07-24": (1900.0, "2026-07-24"), "2026-10-22": (1850.0, "2026-10-22")}


def fx_stub(currency, day):
    return RATES[day]


def det_codes(case_id):
    row = next(r for r in planted_rows() if r["case_id"] == case_id)
    claim, pool = build(row)
    return row, {f.code for f in deterministic_checks(claim, pool, CATALOG, now=NOW, max_age_days=30, fx_rate_fn=fx_stub)}


@pytest.mark.parametrize("case_id", ["F01", "F02", "F05", "F06", "F07", "F08", "F09", "F10", "F11", "F12", "F13", "F14"])
def test_deterministic_plants_are_caught_with_the_expected_code(case_id):
    row, codes = det_codes(case_id)
    expected = {"DUPLICATE_SOURCE" if c == "NOT_INDEPENDENT" else c for c in row["expected_codes"].split(";")}  # NOT_INDEPENDENT was renamed
    if case_id == "F02":  # a flipped direction is now caught by its own check instead of FX_MISMATCH
        expected.add("DIRECTION_MISMATCH")
    assert expected & codes, (case_id, codes)


@pytest.mark.parametrize("case_id", ["C01", "C02", "C03", "C04", "C05", "F17", "F19", "F20"])
def test_controls_and_semantic_plants_clear_the_deterministic_checks(case_id):
    """Controls must reach the model check; the semantic plants (cause / date) are meant to be judged by it (or missed)."""
    assert det_codes(case_id)[1] == set(), case_id


@pytest.mark.parametrize("case_id,code", [("F04", "CURRENCY_COUNTRY_MISMATCH"), ("F18", "NUMBER_NOT_IN_SOURCE"),
                                          ("F15", "GBP_MISMATCH")])
def test_plants_that_used_to_slip_past_the_deterministic_checks_are_now_caught(case_id, code):
    """F04, F15, F16 and F18 cleared the deterministic checks before the number checks existed."""
    assert code in det_codes(case_id)[1]


def test_classify_and_score():
    rows = {r["case_id"]: r for r in planted_rows()}
    sb = Verdict("SEND_BACK", ["COUNTRY_MISMATCH: x"])
    ok = Verdict("PASS", ["fine"])
    unparsed = Verdict("SEND_BACK", ["NOT_SUPPORTED: The Evaluator's model reply could not be parsed, so the claim is not accepted."])
    results = [planted.classify(rows["F13"], sb), planted.classify(rows["F15"], ok), planted.classify(rows["F17"], None, "InternalServerError: 502"),
               planted.classify(rows["F18"], unparsed), planted.classify(rows["C01"], ok), planted.classify(rows["C02"], sb),
               planted.classify(rows["C03"], None, "boom")]
    assert [r["outcome"] for r in results] == ["CAUGHT", "MISSED", "NOT_EVALUATED", "NOT_EVALUATED", "CORRECTLY_PASSED",
                                               "WRONGLY_REJECTED", "NOT_EVALUATED"]
    assert results[0]["right_reason"] is True
    s = planted.score(results)
    assert (s["caught"], s["missed"], s["false_not_evaluated"]) == (1, 1, 2)
    assert (s["wrongly_rejected"], s["correctly_passed"], s["control_not_evaluated"]) == (1, 1, 1)


def test_planted_report_counts_runtime_errors_separately():
    rows = {r["case_id"]: r for r in planted_rows()}
    results = [planted.classify(rows["F13"], Verdict("SEND_BACK", ["COUNTRY_MISMATCH: x"])), planted.classify(rows["F17"], None, "502")]
    text = planted.render_report(results, {"started": "t", "trace": "logs/a", "results_file": "logs/b", "lane": "l", "retry": "r",
                                           "llm_fail": 3, "llm_ok": 1})
    assert "False claims caught: 1/2" in text and "Could not be evaluated because of runtime errors: 1" in text
    assert "synthetic" in text


# ------------------------------------------------------------------ backtest maths
def test_label_direction_flat_band():
    assert [backtest.label_direction(x) for x in (2.01, 2.0, 0.0, -2.0, -2.01)] == ["up", "flat", "flat", "flat", "down"]


def test_outcome_pct_weights_by_spend():
    # USD became 10 % dearer in pounds (1.0 -> 0.9090909 units per GBP), EUR unchanged; equal spend -> about +5 %
    pct = backtest.outcome_pct({"USD": 100.0, "EUR": 100.0}, {"USD": 1.0, "EUR": 1.0}, {"USD": 1 / 1.1, "EUR": 1.0})
    assert pct == pytest.approx(5.0)
    assert backtest.outcome_pct({"USD": 100.0}, {"USD": 1.2}, {"USD": 1.2}) == 0


def test_taiwan_is_proxied_by_usd():
    assert backtest.OUTCOME_CURRENCY["Taiwan"] == "USD"
    t = [SimpleNamespace(country="Taiwan", quarterly_spend_gbp=5.0), SimpleNamespace(country="China", quarterly_spend_gbp=2.0),
         SimpleNamespace(country="Taiwan", quarterly_spend_gbp=1.0)]
    assert backtest.spend_by_currency(t) == {"USD": 6.0, "CNY": 2.0}


def fake_report(impacts):
    items = [{"delegation": {"work_id": f"D{i}", "analyst": "currency", "part_id": f"P00{i}", "action": "run", "status": "PASS" if imp else "UNVERIFIED"},
              "impact": imp} for i, imp in enumerate(impacts, 1)]
    return {"items": items}


def imp(likely, best, worst, direction="cost_up"):
    return {"likely_gbp": likely, "best_gbp": best, "worst_gbp": worst, "direction": direction, "confidence": "Medium"}


def test_aggregate_vidhur_sums_verified_claims_only():
    agg = backtest.aggregate_vidhur(fake_report([imp(30_000, 10_000, 50_000), None, imp(-5_000, -2_000, -8_000, "cost_down")]), 1_000_000)
    assert agg["n_verified"] == 2 and agg["pct"] == 2.5 and agg["direction"] == "up"
    assert agg["range_gbp"] == [8_000, 42_000]


def test_aggregate_with_nothing_verified_predicts_flat():
    agg = backtest.aggregate_vidhur(fake_report([None, None]), 1_000_000)
    assert agg["direction"] == "flat" and agg["n_verified"] == 0
    assert backtest.contains(agg["range_gbp"], 0.0, 0) is None


def test_contains():
    assert backtest.contains([-10, 50], 20, 2) is True
    assert backtest.contains([50, -10], 20, 2) is True
    assert backtest.contains([-10, 50], 60, 2) is False


def test_incomplete_reason_flags_runtime_errors():
    ok = fake_report([imp(1, 1, 1)])
    assert backtest.incomplete_reason(ok, []) is None
    assert "planning" in backtest.incomplete_reason(ok, [{"stage": "planning", "error": "502"}])
    bad = fake_report([None])
    bad["items"][0]["delegation"].update(status="NOT_ASSESSED", error="InternalServerError")
    assert "InternalServerError" in backtest.incomplete_reason(bad, [])


def test_parse_model_only():
    assert backtest.parse_model_only({"change_pct_low": "3", "change_pct_high": 1}) == (1.0, 3.0)
    assert backtest.parse_model_only({"change_pct_low": "x"}) is None
    assert backtest.parse_model_only({}) is None


def test_scoring_keeps_not_completed_apart():
    rows = [{"naive": {"status": "completed", "correct": True}, "model_only": {"status": "not_completed"},
             "vidhur": {"status": "completed", "correct": False, "contained": True}},
            {"naive": {"status": "completed", "correct": False}, "model_only": {"status": "completed", "correct": True, "contained": False},
             "vidhur": {"status": "not_completed"}}]
    s = backtest.score(rows)
    assert (s["model_only"]["correct"], s["model_only"]["completed"], s["model_only"]["not_completed"]) == (1, 1, 1)
    assert (s["vidhur"]["correct"], s["vidhur"]["completed"], s["vidhur"]["not_completed"]) == (0, 1, 1)
    assert s["vidhur"]["range_contained"] == 1


def test_backtest_report_states_the_limits():
    row = {"case_id": "B1", "as_of": "2021-02-01", "theme": "t", "outcome": {"pct": 1.0, "direction": "flat", "gbp": 100.0, "by_currency": {"USD": 1.0}},
           "naive": {"status": "completed", "direction": "flat", "correct": True},
           "model_only": {"status": "not_completed", "error": "502"}, "vidhur": {"status": "not_completed", "error": "planning: 502"},
           "trace_vidhur": "logs/a", "trace_model_only": "logs/b"}
    llm = {"ok": 0, "failed_attempts": 4, "status_codes": {"502": 4}, "routed_models": [], "answered_models": [], "receipt_ids": []}
    text = backtest.render_report([row], {"started": "x", "lane": "l", "retry": "r", "llm": llm})
    for needle in ("5 cases", "one run", "synthetic", "not completed", "proxied by USD", "502"):
        assert needle in text


# ------------------------------------------------------------------ as-of guards
def test_last_complete_quarter():
    q = asof.last_complete_quarter
    assert q(date(2021, 2, 1)) == "2020-Q4" and q(date(2022, 8, 2)) == "2022-Q2" and q(date(2022, 9, 30)) == "2022-Q3"
    assert q(date(2022, 12, 31)) == "2022-Q4" and q(date(2022, 1, 1)) == "2021-Q4"


def test_sql_guard_blocks_future_and_unbounded_queries():
    d = date(2022, 8, 2)
    asof.check_sql("SELECT 1", d)
    asof.check_sql("SELECT * FROM ganga.parts", d)
    asof.check_sql("SELECT x FROM ganga.purchases WHERE quarter = '2022-Q2'", d)
    asof.check_sql("SELECT MAX(quarter) FROM ganga.purchases WHERE quarter <= '2022-Q2'", d)
    with pytest.raises(asof.AsOfViolation):
        asof.check_sql("SELECT MAX(quarter) FROM ganga.purchases", d)
    with pytest.raises(asof.AsOfViolation):
        asof.check_sql("SELECT x FROM ganga.purchases WHERE quarter = '2022-Q3'", d)


def test_asof_zetaris_wrapper_blocks_before_the_query_runs():
    calls = []
    fake = SimpleNamespace(query=lambda sql: calls.append(sql) or [], health_check=lambda: True)
    zt = asof.AsOfZetaris(fake, date(2022, 8, 2))
    assert zt.max_quarter == "2022-Q2" and zt.health_check()
    zt.query("SELECT * FROM ganga.purchases WHERE quarter = '2022-Q2'")
    with pytest.raises(asof.AsOfViolation):
        zt.query("SELECT * FROM ganga.purchases WHERE quarter = '2026-Q3'")
    assert len(calls) == 1


def test_fx_end_guard():
    asof.guard_fx_end(date(2022, 8, 2), date(2022, 8, 2))
    with pytest.raises(asof.AsOfViolation):
        asof.guard_fx_end(date(2022, 8, 3), date(2022, 8, 2))
    with pytest.raises(asof.AsOfViolation):
        asof.guard_fx_end(None, date(2022, 8, 2))


def test_article_guard():
    from core.news import Article
    old = Article("a", "CNBC", "https://x/a", "2022-08-01T00:00:00+00:00")
    new = Article("b", "CNBC", "https://x/b", "2022-08-03T00:00:00+00:00")
    assert asof.guard_articles([old], date(2022, 8, 2)) == [old]
    with pytest.raises(asof.AsOfViolation):
        asof.guard_articles([old, new], date(2022, 8, 2))


def test_prepared_backtest_files_are_consistent_and_leak_free():
    import csv
    cases = {r["case_id"]: date.fromisoformat(r["as_of"]) for r in csv.DictReader(open(backtest.CASES_CSV, encoding="utf-8"))}
    assert len(cases) == 5
    for cid, d in cases.items():
        arts = asof.load_news(cid, d)  # raises if any headline is dated after as_of
        assert arts, cid
        assert all(a.published[:10] >= str(d - timedelta(days=45)) for a in arts)
    rows = list(csv.DictReader(open(asof.NEWS_CSV, encoding="utf-8")))
    assert all(r["url"].startswith("https://") and r["data_status"] == "prepared evaluation data" for r in rows)
    assert {r["case_id"] for r in rows} == set(cases)


def test_load_news_rejects_a_future_headline(tmp_path):
    p = tmp_path / "n.csv"
    p.write_text("case_id,as_of,published,publisher,title,url,data_status\nB1,2022-08-02,2022-08-05,CNBC,t,https://x,prepared evaluation data\n", encoding="utf-8")
    with pytest.raises(asof.AsOfViolation):
        asof.load_news("B1", date(2022, 8, 2), path=p)


# ------------------------------------------------------------------ trace summary and the llm retry knobs
def test_llm_summary_reads_failures_and_routing(tmp_path):
    p = tmp_path / "t.jsonl"
    events = [{"step": "llm:claim:error", "status_code": 502, "routed_model": "nvidia/m", "receipt_id": None},
              {"step": "llm:claim", "answered_model": "nvidia/m", "receipt_id": "r-1"},
              {"step": "verdict"}]
    p.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    s = llm_summary(p)
    assert (s["ok"], s["failed_attempts"], s["status_codes"], s["routed_models"], s["receipt_ids"]) == (1, 1, {"502": 1}, ["nvidia/m"], ["r-1"])


def test_error_details_reads_routed_model():
    from core import llm
    exc = SimpleNamespace(status_code=502, body={"meterless_detail": "nvidia/nemotron-x_empty_response", "receipt_id": "abc"}, response=None)
    assert llm.error_details(exc) == {"status_code": 502, "routed_model": "nvidia/nemotron-x", "receipt_id": "abc"}


# ------------------------------------------------------------------ Zetaris transient-error retry
def test_zetaris_retries_a_transient_400(monkeypatch):
    from core import zetaris_client as zc
    monkeypatch.setattr(zc, "QUERY_RETRY_WAIT_SECONDS", 0)
    answers = iter([SimpleNamespace(status_code=400, text="bad"), SimpleNamespace(status_code=200, json=lambda: {"headers": ["q"], "data": [["2022-Q2"]]})])
    client = zc.ZetarisClient()
    client._token = "t"
    monkeypatch.setattr(client, "_send", lambda *a, **k: next(answers))
    assert client.query("SELECT 1 AS q") == [{"q": "2022-Q2"}]
