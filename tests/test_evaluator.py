"""Deterministic Evaluator checks. No network, no Zetaris, no model calls."""

from datetime import datetime, timezone

from agents.evaluator import deterministic_checks
from core.claims import Claim
from core.news import Article

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)
CATALOG = {"P001": {"part_name": "GangaAccel GA100 GPU Chip", "country": "Taiwan"},
           "P002": {"part_name": "GangaHBM-8H Memory Stack", "country": "South Korea"}}


def article(n, publisher, title, published="2026-10-20T10:00:00+00:00"):
    return Article(title=title, publisher=publisher, url=f"https://example.test/{n}", published=published)


POOL = {a.url: a for a in [
    article(1, "Reuters", "Taiwan tightens chip export rules after US talks"),
    article(2, "Bloomberg", "Taipei to curb AI chip shipments under trade deal"),
    article(3, "Reuters", "Taiwan chip makers brace for new export licences"),
    article(4, "The Guardian", "Taiwan tightens chip export rules after US talks!"),   # near-identical headline to #1
    article(5, "BBC News", "Old story about chip tariffs", published="2026-08-01T10:00:00+00:00"),  # > 30 days old
]}


def claim(urls, part_id="P001", country="Taiwan", direction="cost_up", low=3.0, high=8.0):
    return Claim("C1", "Export curbs raise GPU chip costs.", part_id, "x", country, direction, low, high, list(urls))


def codes(c):
    return {f.code for f in deterministic_checks(c, POOL, CATALOG, now=NOW, max_age_days=30)}


def test_good_claim_has_no_findings():
    assert codes(claim(["https://example.test/1", "https://example.test/2"])) == set()


def test_fewer_than_two_sources_is_sent_back():
    assert "TOO_FEW_SOURCES" in codes(claim(["https://example.test/1"]))
    assert "TOO_FEW_SOURCES" in codes(claim([]))


def test_same_url_cited_twice_counts_once():
    assert "TOO_FEW_SOURCES" in codes(claim(["https://example.test/1", "https://example.test/1"]))


def test_same_publisher_twice_is_not_independent():
    assert "DUPLICATE_SOURCE" in codes(claim(["https://example.test/1", "https://example.test/3"]))


def test_syndicated_story_under_different_publishers_is_not_independent():
    assert "DUPLICATE_SOURCE" in codes(claim(["https://example.test/1", "https://example.test/4"]))


def test_stale_source_is_sent_back():
    assert "STALE_SOURCE" in codes(claim(["https://example.test/2", "https://example.test/5"]))


def test_invented_url_is_sent_back():
    assert "UNKNOWN_URL" in codes(claim(["https://example.test/1", "https://made-up.test/story"]))


def test_part_and_country_must_exist_and_match_ganga_data():
    urls = ["https://example.test/1", "https://example.test/2"]
    assert "UNKNOWN_PART" in codes(claim(urls, part_id="P999"))
    assert "COUNTRY_MISMATCH" in codes(claim(urls, country="South Korea"))
    assert "UNKNOWN_COUNTRY" in codes(claim(urls, country="Atlantis"))


def test_bad_direction_or_range_is_sent_back():
    urls = ["https://example.test/1", "https://example.test/2"]
    assert "BAD_DIRECTION" in codes(claim(urls, direction="sideways"))
    assert "BAD_RANGE" in codes(claim(urls, low=9, high=2))
    assert "BAD_RANGE" in codes(claim(urls, low=float("nan"), high=5))
