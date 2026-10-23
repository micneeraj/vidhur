"""Source trust: map a URL (or a publisher name) to (publisher, tier, score) from the curated list in data/source_trust.csv.

Tier 1 = 1.0 (wires, top business press, official sources), Tier 2 = 0.7 (known business / trade press),
Tier 3 = 0.3 (anything unlisted). Subdomains match their parent (uk.reuters.com -> reuters.com). Google News links
point at news.google.com, not the publisher, so for aggregator and placeholder hosts the publisher name decides.
"""

import csv
import re
from functools import lru_cache
from urllib.parse import urlparse

from core import config

TRUST_CSV = config.ROOT / "data" / "source_trust.csv"
UNKNOWN_TIER, UNKNOWN_SCORE = 3, 0.3
AGGREGATOR_HOSTS = {"news.google.com", "google.com", "news.yahoo.com", "msn.com", "flipboard.com", "newsbreak.com"}
PLACEHOLDER_SUFFIXES = (".test", ".example", ".invalid", ".localhost")
# Official domains by suffix, so every government site does not need a row.
OFFICIAL_SUFFIXES = (".gov", ".gov.uk", ".gov.in", ".gov.tw", ".go.kr", ".go.jp", ".europa.eu")
TWO_PART_TLDS = {"co.uk", "co.jp", "co.kr", "com.au", "co.in", "com.tw", "org.uk", "gov.uk", "ac.uk", "com.cn", "com.sg", "co.nz"}


@lru_cache(maxsize=1)
def load_trust(path=None):
    """Rows of the trust list: {domain: (publisher, tier, score)}."""
    rows = {}
    with open(path or TRUST_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows[row["domain"].strip().lower()] = (row["publisher"].strip(), int(row["tier"]), float(row["score"]))
    return rows


def _norm_name(name):
    name = re.sub(r"^www\.", "", (name or "").lower().strip())
    name = re.sub(r"^the\s+", "", name)
    name = re.sub(r"\s+news$", "", name)
    return re.sub(r"[^a-z0-9]", "", name)


@lru_cache(maxsize=1)
def _by_publisher():
    return {_norm_name(pub): domain for domain, (pub, _, _) in load_trust().items()}


def host_of(url):
    host = (urlparse(url if "//" in (url or "") else f"//{url}").hostname or "").lower()
    return re.sub(r"^www\.", "", host)


def is_opaque_host(host):
    """True when the URL host says nothing about the publisher (aggregator link, placeholder host, empty)."""
    return not host or host in AGGREGATOR_HOSTS or host.endswith(PLACEHOLDER_SUFFIXES)


def registrable_root(host):
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in TWO_PART_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _match_domain(host):
    """Longest listed domain that equals the host or is a parent of it."""
    rows = load_trust()
    parts = host.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in rows:
            return candidate
    return None


def lookup(url, publisher=None):
    """Return (listed_domain or None, (publisher, tier, score))."""
    host = host_of(url)
    domain = None if is_opaque_host(host) else _match_domain(host)
    if domain is None and publisher:
        # Google News gives the publisher's name (or sometimes its domain) instead of a publisher URL.
        as_host = host_of(publisher) if "." in publisher and " " not in publisher.strip() else ""
        domain = (_match_domain(as_host) if as_host else None) or _by_publisher().get(_norm_name(publisher))
    if domain is not None:
        return domain, load_trust()[domain]
    if host and not is_opaque_host(host) and host.endswith(OFFICIAL_SUFFIXES):
        return registrable_root(host), (host, 1, 1.0)
    return None, (publisher or registrable_root(host) or "Unknown", UNKNOWN_TIER, UNKNOWN_SCORE)


def trust_of(url, publisher=None):
    """(publisher, tier, score) for a URL. Unknown domains are Tier 3."""
    return lookup(url, publisher)[1]


def publisher_key(url, publisher=None):
    """Stable key for 'same publisher': the listed domain, else the URL's domain root, else the normalised publisher name."""
    domain, _ = lookup(url, publisher)
    if domain:
        return domain
    host = host_of(url)
    return registrable_root(host) if not is_opaque_host(host) else _norm_name(publisher) or host
