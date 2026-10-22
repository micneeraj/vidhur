"""RSS parsing from a fixed sample - no network."""

from core.news import parse_rss
from core.llm import extract_json

GOOGLE_SAMPLE = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Taiwan curbs chip exports - Reuters</title><link>https://news.google.com/rss/articles/abc</link>
<pubDate>Wed, 21 Oct 2026 09:30:00 GMT</pubDate><source url="https://www.reuters.com">Reuters</source></item>
<item><title>No date here</title><link>https://example.test/x</link></item>
</channel></rss>"""


def test_parse_rss_extracts_publisher_title_and_date():
    articles = parse_rss(GOOGLE_SAMPLE, "Google News")
    assert len(articles) == 1  # the item without a date is skipped
    a = articles[0]
    assert a.publisher == "Reuters"
    assert a.title == "Taiwan curbs chip exports"
    assert a.published.startswith("2026-10-21T09:30")


def test_extract_json_ignores_reasoning_text_and_code_fences():
    reply = 'Let me think. An example is {"a": 1}. Final answer:\n```json\n{"claims": [{"x": 2}]}\n```'
    assert extract_json(reply) == {"claims": [{"x": 2}]}
