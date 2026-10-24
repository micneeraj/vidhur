"""The 6 NEW planted claims and the deterministic planted-claim runner. No network: FX rates and the calculator are stubbed."""

import importlib.util
from datetime import datetime, timezone

import pytest

from agents.evaluator import deterministic_checks
from core import calculator
from core.asof import config
from core.fx import FXMove, fx_source_article

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


def load(name):
    spec = importlib.util.spec_from_file_location(name, config.ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


planted, det = load("eval_planted_claims"), load("eval_planted_deterministic")
NEW_ROWS = planted.read_csv(det.NEW_CLAIMS_CSV)
CATALOG = {"P001": {"part_name": "GPU", "country": "Taiwan"}, "P002": {"part_name": "HBM", "country": "South Korea"},
           "P003": {"part_name": "ABF", "country": "Japan"}, "P006": {"part_name": "Cooling", "country": "Germany"}}
MOVE = FXMove("KRW", "2026-07-24", "2026-10-22", 1900.0, 1850.0, 1.5, 2.7, "2026-09-22", "2026-07-24", "https://api.frankfurter.dev/x")
RATES = {"2026-07-24": 1900.0, "2026-10-22": 1850.0}
ASSUMPTIONS = {"SCENARIO_LARGE_MOVE_PCT": 12.0, "SCENARIO_LIKELY_MOVE_PCT": 6.0, "SCENARIO_SMALL_MOVE_PCT": 2.0, "FX_PASS_THROUGH_PCT": 100.0}
BASELINE = {"P001": 800000.0, "P002": 300000.0, "P003": 200000.0, "P006": 100000.0}


def run_case(case_id):
    row = next(r for r in NEW_ROWS if r["case_id"] == case_id)
    pool = planted.build_pool(planted.read_csv(planted.SOURCES_CSV) + planted.read_csv(det.NEW_SOURCES_CSV), NOW)
    art = fx_source_article(MOVE)
    pool[art.url] = art
    claim = planted.build_claim(row, pool, {"KRW": art}, {"KRW": MOVE}, BASELINE, names={"P001": "GPU"})
    if row["fx_mode"] == "bad_rate":
        claim.computed["end_rate"] = round(claim.computed["end_rate"] * 1.08, 4)
    rows = [calculator.PurchaseRow(claim.part_id, "x", claim.supplier_country, 1, BASELINE[claim.part_id] * 1.25, "USD", "2026-Q3")]
    calc = calculator.compute(rows, claim, 1.25, "2026-10-23", "stub", ASSUMPTIONS).calc
    return row, {f.code for f in deterministic_checks(claim, pool, CATALOG, now=NOW, calc=calc, fx_rate_fn=lambda c, d: (RATES[d], d))}


def test_new_file_has_six_cases_marked_new_one_per_check():
    assert [r["case_id"] for r in NEW_ROWS] == ["N01", "N02", "N03", "N04", "N05", "N06"]
    assert {r["status"] for r in NEW_ROWS} == {"NEW"} and all(r["what_is_wrong"].startswith("NEW.") for r in NEW_ROWS)
    assert [r["expected_codes"] for r in NEW_ROWS] == ["NUMBER_NOT_IN_SOURCE", "FX_MISMATCH", "CURRENCY_COUNTRY_MISMATCH", "GBP_MISMATCH",
                                                       "DUPLICATE_SOURCE", "STALE_SOURCE"]
    original_ids = {r["case_id"] for r in planted.read_csv(planted.CLAIMS_CSV)}
    assert not original_ids & {r["case_id"] for r in NEW_ROWS} and len(original_ids) == 25  # the original file was not touched


@pytest.mark.parametrize("case_id", ["N01", "N02", "N03", "N04", "N05", "N06"])
def test_each_new_plant_is_caught_by_its_own_check(case_id):
    row, codes = run_case(case_id)
    assert row["expected_codes"] in codes, (case_id, codes)


def test_failure_types_are_fixed_and_cover_every_case():
    assert det.failure_type("F03") == "fabricated number" and det.failure_type("F04") == "wrong currency"
    assert det.failure_type("F17") == "other" and det.failure_type("N05") == "duplicate source"
    assert set(det.TYPE_OF_CASE.values()) <= set(det.FAILURE_TYPES)


def test_summary_counts_by_type():
    results = [{"case_id": "F01", "group": "false", "outcome": "CAUGHT", "reason_codes": ["FX_MISMATCH"]},
               {"case_id": "F02", "group": "false", "outcome": "MISSED", "reason_codes": []},
               {"case_id": "F15", "group": "false", "outcome": "CAUGHT", "reason_codes": ["NUMBER_NOT_IN_SOURCE"]},
               {"case_id": "C01", "group": "control", "outcome": "CORRECTLY_PASSED", "reason_codes": []}]
    s = det.summarise(results)
    assert s["FX mismatch"] == {"planted": 2, "caught": 1, "intended": 1, "missed": 1, "not_evaluated": 0}
    assert s["GBP mismatch"]["caught"] == 1 and s["GBP mismatch"]["intended"] == 0  # caught, but not by the GBP check
