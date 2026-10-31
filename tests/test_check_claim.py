"""zetaris_demo/check_claim.py: the Evaluator's GBP check against a Zetaris figure. No network (Zetaris is faked)."""

import pytest

from zetaris_demo import check_claim

ROW = {"part_id": "P002", "supplier_country": "South Korea", "currency": "USD", "rate_per_gbp": "1.3399",
       "rate_date": "2026-10-30", "gbp_exposure_live": "328502.13"}


class FakeZetaris:
    def __init__(self, rows):
        self.rows, self.sql = rows, None

    def query(self, sql, agent=None):
        self.sql = sql
        return self.rows


def test_ten_times_too_large_is_sent_back():
    passed, reasons, row, sql = check_claim.check(check_claim.DEMO_CLAIMS[0], FakeZetaris([ROW]))
    assert not passed and reasons[0].startswith("GBP_MISMATCH")
    assert "p.part_id = 'P002'" in sql and row["gbp_exposure_live"] == "328502.13"


def test_figure_within_tolerance_passes():
    passed, reasons, _, _ = check_claim.check(check_claim.DEMO_CLAIMS[1], FakeZetaris([ROW]))
    assert passed and reasons == []


def test_claim_without_part_or_data_is_an_error():
    with pytest.raises(ValueError):
        check_claim.check("Exposure is GBP 5m", FakeZetaris([ROW]))
    with pytest.raises(ValueError):
        check_claim.check("P009 exposure is GBP 5m", FakeZetaris([]))
