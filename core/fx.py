"""Real exchange-rate data for the Currency Analyst: Frankfurter (European Central Bank reference rates).

Free, no API key. Every number the Currency Analyst reports is computed here in Python from this data;
the model only explains it. https://frankfurter.dev
"""

import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import requests

from core.news import Article

API = "https://api.frankfurter.dev/v1"
TIMEOUT = 20
PUBLISHER = "Frankfurter (ECB reference rates)"

# The currency of each supplier country in Ganga's data. Taiwan's dollar (TWD) is NOT published by
# the ECB, so the API cannot cover it; the analyst reports that instead of guessing.
COUNTRY_CURRENCY = {
    "Taiwan": "TWD", "South Korea": "KRW", "Japan": "JPY", "China": "CNY", "Germany": "EUR", "Malaysia": "MYR",
}
# Currencies a claim about a supplier country may legitimately cite. Taiwan's dollar is not in the ECB set, so USD is
# accepted there as the usual proxy (Taiwanese chip makers invoice in US dollars).
COUNTRY_CURRENCIES = {
    "Taiwan": {"TWD", "USD"}, "South Korea": {"KRW"}, "Japan": {"JPY"}, "China": {"CNY"}, "Germany": {"EUR"}, "Malaysia": {"MYR"},
}
CURRENCY_NAME = {"TWD": "Taiwan dollar", "KRW": "South Korean won", "JPY": "Japanese yen", "CNY": "Chinese yuan",
                 "EUR": "euro", "MYR": "Malaysian ringgit", "USD": "US dollar"}

_cache = {}
_cache_lock = threading.Lock()


class FXUnavailable(Exception):
    pass


@dataclass
class FXMove:
    currency: str
    start_date: str
    end_date: str
    start_rate: float  # units of currency per 1 GBP
    end_rate: float
    move_30d_pct: float  # % change in the GBP cost of 1 unit of the currency (positive = currency dearer)
    move_90d_pct: float
    date_30d: str
    date_90d: str
    url: str

    def to_dict(self):
        return self.__dict__.copy()


def supported_currencies():
    with _cache_lock:
        if "currencies" in _cache:
            return _cache["currencies"]
    r = requests.get(f"{API}/currencies", timeout=TIMEOUT)
    r.raise_for_status()
    codes = set(r.json())
    with _cache_lock:
        _cache["currencies"] = codes
    return codes


def _rate_on_or_before(rates, target):
    days = sorted(d for d in rates if d <= target)
    if not days:
        raise FXUnavailable("no rate on or before " + target)
    return days[-1], days[-1]


def rate_on(currency, on_date=None):
    """(units of `currency` per 1 GBP, the ECB publication date actually used) on or before `on_date` (default: latest)."""
    key = ("rate", currency, str(on_date))
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    if on_date is None:
        url = f"{API}/latest?base=GBP&symbols={currency}"
    else:
        # ask for a short window so a weekend or holiday resolves to the last published day
        start = datetime.fromisoformat(str(on_date)).date() - timedelta(days=7)
        url = f"{API}/{start}..{on_date}?base=GBP&symbols={currency}"
    r = requests.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    body = r.json()
    if on_date is None:
        result = (float(body["rates"][currency]), body["date"])
    else:
        rates = {d: v[currency] for d, v in body.get("rates", {}).items() if currency in v}
        if not rates:
            raise FXUnavailable(f"no {currency} rate published on or before {on_date}")
        day = max(rates)
        result = (float(rates[day]), day)
    with _cache_lock:
        _cache[key] = result
    return result


def pct_change_in_gbp_cost(start_rate, end_rate):
    """If 1 GBP buys fewer units of the currency (end < start) the currency became dearer in GBP."""
    return (start_rate / end_rate - 1) * 100


def fetch_fx_move(currency, end=None):
    """30- and 90-day move of `currency` against GBP, computed from ECB daily reference rates."""
    if currency not in supported_currencies():
        raise FXUnavailable(f"{currency} is not published by the ECB / Frankfurter")
    end_day = end or date.today()
    start_day = end_day - timedelta(days=90)
    key = (currency, str(end_day))
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    url = f"{API}/{start_day}..{end_day}?base=GBP&symbols={currency}"
    r = requests.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    rates = {d: v[currency] for d, v in r.json()["rates"].items()}
    if len(rates) < 20:
        raise FXUnavailable("too few daily rates returned")
    last_day = max(rates)
    d30, _ = _rate_on_or_before(rates, str(datetime.fromisoformat(last_day).date() - timedelta(days=30)))
    d90 = min(rates)
    move = FXMove(
        currency=currency, start_date=d90, end_date=last_day, start_rate=rates[d90], end_rate=rates[last_day],
        move_30d_pct=round(pct_change_in_gbp_cost(rates[d30], rates[last_day]), 2),
        move_90d_pct=round(pct_change_in_gbp_cost(rates[d90], rates[last_day]), 2),
        date_30d=d30, date_90d=d90, url=url,
    )
    with _cache_lock:
        _cache[key] = move
    return move


def fx_source_article(move):
    """The FX API call, as a citable source with its date (so the Evaluator can check it like any other)."""
    return Article(
        title=(f"GBP/{move.currency} ECB reference rates {move.date_90d} to {move.end_date}: "
               f"{move.move_90d_pct:+.2f}% over 90 days, {move.move_30d_pct:+.2f}% over 30 days"),
        publisher=PUBLISHER, url=move.url,
        published=datetime.fromisoformat(move.end_date).replace(tzinfo=timezone.utc).isoformat(timespec="seconds"),
        summary=f"1 GBP = {move.end_rate} {move.currency} on {move.end_date} (was {move.start_rate} on {move.date_90d}).",
        feed="frankfurter-api",
    )
