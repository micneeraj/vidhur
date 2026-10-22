"""Free, real news evidence from RSS feeds (Google News search plus reputable publisher feeds).

The cache in data/news_cache/ stores RAW fetched articles only, with the fetch timestamp.
It never stores analyst claims or final answers.
"""

import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests

from core import config

CACHE_DIR = config.ROOT / "data" / "news_cache"
USER_AGENT = "Mozilla/5.0 (Vidhur hackathon research agent; RSS reader)"
REQUEST_TIMEOUT = 20

GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}&hl=en-GB&gl=GB&ceid=GB:en"

# Freely available RSS feeds from reputable publishers. Filtered by keyword locally.
PUBLISHER_FEEDS = {
    "BBC News": [
        "http://feeds.bbci.co.uk/news/business/rss.xml",
        "http://feeds.bbci.co.uk/news/world/rss.xml",
        "http://feeds.bbci.co.uk/news/technology/rss.xml",
    ],
    "The Guardian": [
        "https://www.theguardian.com/business/rss",
        "https://www.theguardian.com/world/rss",
        "https://www.theguardian.com/technology/rss",
    ],
    "Al Jazeera": ["https://www.aljazeera.com/xml/rss/all.xml"],
    "NPR": ["https://feeds.npr.org/1006/rss.xml"],
    "DW": ["https://rss.dw.com/rdf/rss-en-bus"],
}


@dataclass
class Article:
    title: str
    publisher: str
    url: str
    published: str  # ISO 8601 UTC
    summary: str = ""
    feed: str = ""

    def to_dict(self):
        return asdict(self)


# --------------------------------------------------------------------------- parsing
def _clean(text):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return " ".join(text.split())


def _parse_date(value):
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_rss(xml_text, default_publisher="", feed=""):
    """Parse RSS 2.0 (and RSS 1.0/RDF) text into Articles. Items without a link or date are skipped."""
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        return []
    articles = []
    for item in root.iter():
        if not item.tag.endswith("item"):
            continue
        fields = {child.tag.split("}")[-1]: child for child in item}
        title = _clean(fields["title"].text) if "title" in fields else ""
        link = (fields["link"].text or "").strip() if "link" in fields else ""
        date_node = fields.get("pubDate") if "pubDate" in fields else fields.get("date")
        published = _parse_date(date_node.text) if date_node is not None else None
        if not (title and link and published):
            continue
        publisher = default_publisher
        if "source" in fields and (fields["source"].text or "").strip():
            publisher = _clean(fields["source"].text)  # Google News names the real publisher here
            suffix = f" - {publisher}"
            if title.endswith(suffix):
                title = title[: -len(suffix)]
        summary = _clean(fields["description"].text) if "description" in fields else ""
        if default_publisher == "Google News" and "source" in fields:
            summary = ""  # Google's description only repeats the headline
        articles.append(
            Article(
                title=title,
                publisher=publisher or "Unknown",
                url=link,
                published=published.isoformat(timespec="seconds"),
                summary=summary[:500],
                feed=feed,
            )
        )
    return articles


# --------------------------------------------------------------------------- fetching
def _get(url):
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.content


def _keywords(query):
    stop = {"or", "and", "the", "of", "in", "to", "a"}
    return [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z-]{2,}", query) if w.lower() not in stop]


def _matches(article, words):
    haystack = f"{article.title} {article.summary}".lower()
    return sum(1 for w in words if w in haystack) >= min(2, len(words)) if words else True


def _fetch_live(query, max_age_days):
    """Fetch from Google News search and the publisher feeds. Returns (articles, errors)."""
    articles, errors = [], []
    google_query = quote_plus(f"{query} when:{max_age_days}d")
    try:
        articles += parse_rss(_get(GOOGLE_NEWS.format(q=google_query)), "Google News", "google-news-search")
    except Exception as exc:  # a dead feed must not stop the run
        errors.append(f"google-news: {type(exc).__name__}")

    words = _keywords(query)
    for publisher, urls in PUBLISHER_FEEDS.items():
        for url in urls:
            try:
                found = parse_rss(_get(url), publisher, url)
            except Exception as exc:
                errors.append(f"{publisher}: {type(exc).__name__}")
                continue
            articles += [a for a in found if _matches(a, words)]
    return articles, errors


def _dedupe(articles):
    seen, unique = set(), []
    for a in articles:
        key = a.url or a.title.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(a)
    return unique


def _cache_path(query):
    digest = hashlib.sha1(query.lower().encode("utf-8")).hexdigest()[:10]
    slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:40]
    return CACHE_DIR / f"{slug}_{digest}.json"


def fetch_news(query, max_age_days=None, refresh=False):
    """Return (articles, info) for a query.

    info = {"fetched_at": iso, "from_cache": bool, "errors": [...]}.
    Articles older than max_age_days are dropped. The cache is reused while it is younger than
    NEWS_CACHE_MAX_AGE_HOURS (default 24); pass refresh=True to force a live fetch.
    """
    max_age_days = max_age_days or config.evidence_max_age_days()
    now = datetime.now(timezone.utc)
    path = _cache_path(query)
    ttl = timedelta(hours=float(config.env("NEWS_CACHE_MAX_AGE_HOURS", "24")))

    if path.exists() and not refresh:
        cached = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(cached["fetched_at"])
        if now - fetched_at < ttl:
            articles = [Article(**a) for a in cached["articles"]]
            return _recent(articles, max_age_days, now), {"fetched_at": cached["fetched_at"], "from_cache": True, "errors": []}

    articles, errors = _fetch_live(query, max_age_days)
    articles = _dedupe(articles)
    fetched_at = now.isoformat(timespec="seconds")
    if articles:  # never overwrite a good cache with an empty result from a network failure
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"query": query, "fetched_at": fetched_at, "articles": [a.to_dict() for a in articles]}, indent=1, ensure_ascii=False),
            encoding="utf-8",
        )
    return _recent(articles, max_age_days, now), {"fetched_at": fetched_at, "from_cache": False, "errors": errors}


def _recent(articles, max_age_days, now):
    cutoff = now - timedelta(days=max_age_days)
    kept = [a for a in articles if datetime.fromisoformat(a.published) >= cutoff]
    return sorted(kept, key=lambda a: a.published, reverse=True)
