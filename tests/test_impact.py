"""GBP impact maths and confidence levels."""

import pytest

from core.impact import compute_impact, confidence_level


def test_cost_up_range():
    imp = compute_impact(1_000_000, 4, 10, "cost_up")
    assert imp.worst_gbp == 100_000.00
    assert imp.likely_gbp == 70_000.00
    assert imp.best_gbp == 40_000.00


def test_cost_down_range_is_a_saving_and_worst_is_smallest_saving():
    imp = compute_impact(1_000_000, 4, 10, "cost_down")
    assert imp.worst_gbp == -40_000.00
    assert imp.likely_gbp == -70_000.00
    assert imp.best_gbp == -100_000.00
    assert imp.worst_gbp > imp.likely_gbp > imp.best_gbp


def test_rounds_to_pence():
    imp = compute_impact(123_456.78, 1.5, 2.5, "cost_up")
    assert imp.worst_gbp == 3086.42 and imp.best_gbp == 1851.85 and imp.likely_gbp == 2469.14


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        compute_impact(1000, 5, 2, "cost_up")
    with pytest.raises(ValueError):
        compute_impact(1000, 1, 2, "sideways")


@pytest.mark.parametrize("sources,retries,strength,expected", [
    (3, 0, "strong", "High"),
    (4, 0, "moderate", "High"),
    (2, 0, "strong", "Medium"),
    (3, 1, "moderate", "Medium"),
    (2, 1, "moderate", "Low"),
    (3, 1, "weak", "Low"),
    (2, 2, "weak", "Low"),
])
def test_confidence_levels(sources, retries, strength, expected):
    assert confidence_level(sources, retries, strength) == expected
