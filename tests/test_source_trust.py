"""Source trust ranking and the Evaluator trust rules. No network, no Zetaris, no model calls."""

from datetime import datetime, timedelta, timezone

from agents.evaluator import deterministic_checks, independent_groups, source_profile
from core.claims import Claim
from core.news import Article, rank_by_trust, score_articles
from core.source_trust import publisher_key, trust_of

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)
CATALOG = {"P001": {"part_name": "GangaAccel GA100 GPU Chip", "country": "Taiwan"}}
FRESH = "2026-10-20T10:00:00+00:00"


def art(url, publisher, title, published=FRESH):
    return Article(title=title, publisher=publisher, url=url, published=published)


def check(articles):
    pool = {a.url: a for a in articles}
    c = Claim("C1", "Export curbs raise GPU chip costs.", "P001", "x", "Taiwan", "cost_up", 3.0, 8.0, list(pool))
    return {f.code for f in deterministic_checks(c, pool, CATALOG, now=NOW, max_age_days=30)}


# ---- URL -> tier -----------------------------------------------------------------------------
def test_tier1_domain():
    assert trust_of("https://www.reuters.com/world/x")[1:] == (1, 1.0)
    assert trust_of("https://www.wto.org/news")[1:] == (1, 1.0)


def test_tier2_domain():
    assert trust_of("https://www.cnbc.com/a")[1:] == (2, 0.7)
    assert trust_of("https://koreajoongangdaily.joins.com/a")[1:] == (2, 0.7)


def test_subdomain_counts_as_parent():
    assert trust_of("https://uk.reuters.com/x")[1] == 1
    assert trust_of("https://asia.nikkei.com/x")[1] == 1
    assert trust_of("https://news.bbc.co.uk/x")[1] == 1


def test_unknown_domain_is_tier3():
    assert trust_of("https://random-blog.example.org/post")[1:] == (3, 0.3)
    assert trust_of("https://notreuters.com/x")[1] == 3  # lookalike, not a subdomain
    assert trust_of("https://reuters.com.evil.io/x")[1] == 3


def test_official_suffix_is_tier1():
    assert trust_of("https://www.bis.doc.gov/entities")[1] == 1
    assert trust_of("https://www.statistics.gov.uk/a")[1] == 1


def test_google_news_link_falls_back_to_publisher_name():
    link = "https://news.google.com/rss/articles/abc"
    assert trust_of(link, "Reuters")[1] == 1
    assert trust_of(link, "The Guardian")[1] == 2
    assert trust_of(link, "BBC News")[1] == 1
    assert trust_of(link, "Some Tiny Blog")[1] == 3


# ---- ranking ---------------------------------------------------------------------------------
def test_combined_score_is_60_40():
    a = art("https://www.reuters.com/a", "Reuters", "Taiwan chip export controls")
    (_, info), = score_articles([a], [(["taiwan"], 3)], NOW)
    assert info["relevance"] == 1.0 and info["trust"] == 1.0 and info["combined"] == 1.0
    b = art("https://blog.example.org/a", "Blog", "Taiwan chip export controls")
    (_, info), = score_articles([b], [(["taiwan"], 3)], NOW)
    assert info["combined"] == round(0.6 * 1.0 + 0.4 * 0.3, 3)


def test_trust_wins_a_relevance_tie():
    same = "Taiwan chip export controls tighten"
    reuters = art("https://www.reuters.com/a", "Reuters", same)
    blog = art("https://blog.example.org/a", "Blog", same)
    assert rank_by_trust([blog, reuters], [(["taiwan"], 3)], k=1, now=NOW) == [reuters]


def test_trust_can_outrank_slightly_better_relevance():
    better = art("https://blog.example.org/b", "Blog", "Taiwan chip export controls tighten tariffs")
    trusted = art("https://www.reuters.com/c", "Reuters", "Taiwan chip export")
    ranked = rank_by_trust([better, trusted], [(["taiwan"], 3), (["tariffs"], 1)], k=2, now=NOW)
    assert ranked[0] == trusted


def test_rank_logs_kept_and_dropped_with_scores():
    class FakeTrace:
        def __init__(self):
            self.events = []

        def event(self, agent, step, **kw):
            self.events.append((step, kw))

    trace = FakeTrace()
    arts = [art(f"https://x{i}.net/a", "Blog", f"Taiwan story {i}") for i in range(5)]
    rank_by_trust(arts, [(["taiwan"], 3)], k=2, now=NOW, trace=trace)
    step, kw = trace.events[0]
    assert step == "evidence:rank" and len(kw["kept"]) == 2 and kw["dropped_total"] == 3
    assert {"tier", "relevance", "trust", "combined"} <= set(kw["dropped"][0])


# ---- independence ----------------------------------------------------------------------------
def test_same_domain_root_counts_once():
    a = art("https://uk.reuters.com/a", "Reuters UK", "Chip curbs spread")
    b = art("https://www.reuters.com/b", "Reuters", "Taiwan limits GPU exports")
    assert len(independent_groups([a, b])) == 1
    assert publisher_key(a.url, a.publisher) == publisher_key(b.url, b.publisher) == "reuters.com"


def test_near_identical_headlines_count_once():
    a = art("https://www.reuters.com/a", "Reuters", "Taiwan tightens chip export rules after US talks")
    b = art("https://www.cnbc.com/b", "CNBC", "Taiwan tightens chip export rules after US talks!")
    assert len(independent_groups([a, b])) == 1
    assert source_profile([a, b])["independent"] == 1


def test_different_publishers_are_independent_and_profile_reports_tiers():
    a = art("https://www.reuters.com/a", "Reuters", "Taiwan tightens chip export rules")
    b = art("https://www.cnbc.com/b", "CNBC", "Seoul weighs memory tariffs")
    p = source_profile([a, b])
    assert p["independent"] == 2 and p["summary"] == "2 independent sources, 1 Tier 1, 1 Tier 2"


# ---- freshness -------------------------------------------------------------------------------
def test_stale_sources_do_not_count():
    old = (NOW - timedelta(days=60)).isoformat()
    codes = check([art("https://www.reuters.com/a", "Reuters", "Old chip story", old),
                   art("https://www.cnbc.com/b", "CNBC", "Different chip story", FRESH)])
    assert "STALE_SOURCES" in codes and "STALE_SOURCE" in codes


def test_one_stale_source_is_fine_when_two_fresh_independent_remain():
    old = (NOW - timedelta(days=60)).isoformat()
    codes = check([art("https://www.reuters.com/a", "Reuters", "Old chip story", old),
                   art("https://www.cnbc.com/b", "CNBC", "Chip story B", FRESH),
                   art("https://www.bloomberg.com/c", "Bloomberg", "Entirely other chip tale", FRESH)])
    assert "STALE_SOURCES" not in codes


def test_a_stale_trusted_source_does_not_satisfy_the_trusted_rule():
    old = (NOW - timedelta(days=60)).isoformat()
    codes = check([art("https://www.reuters.com/a", "Reuters", "Old chip story", old),
                   art("https://blogone.net/b", "Blog One", "Fresh gossip about chips"),
                   art("https://blogtwo.net/c", "Blog Two", "More fresh musings")])
    assert "NO_TRUSTED_SOURCE" in codes


# ---- the new reason codes --------------------------------------------------------------------
def test_no_trusted_source():
    codes = check([art("https://blogone.net/a", "Blog One", "Chip gossip"),
                   art("https://blogtwo.net/b", "Blog Two", "Other chip talk")])
    assert codes == {"NO_TRUSTED_SOURCE"}


def test_one_trusted_source_is_enough():
    codes = check([art("https://www.reuters.com/a", "Reuters", "Taiwan export curbs"),
                   art("https://blogtwo.net/b", "Blog Two", "Other chip talk")])
    assert codes == set()


def test_sources_not_independent():
    codes = check([art("https://www.reuters.com/a", "Reuters", "Taiwan export curbs"),
                   art("https://uk.reuters.com/b", "Reuters UK", "A different headline altogether")])
    assert "SOURCES_NOT_INDEPENDENT" in codes and "NO_TRUSTED_SOURCE" not in codes


def test_too_few_sources_is_separate_from_the_trust_codes():
    codes = check([art("https://www.reuters.com/a", "Reuters", "Taiwan export curbs")])
    assert "TOO_FEW_SOURCES" in codes and "NO_TRUSTED_SOURCE" not in codes
