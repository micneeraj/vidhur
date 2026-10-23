"""Point-in-time guards for the backtest: Vidhur may only see data dated on or before the case's as_of date.

These are enforced in code (they raise AsOfViolation), not in the prompt:
  - news:      historical headlines are read from data/eval/backtest_news.csv and every article is checked against as_of
  - FX:        the ECB window may not end after as_of (and the data returned may not either)
  - Zetaris:   a query on the purchase table must name a quarter, and that quarter must have ended on or before as_of
"""

import csv
import re
from datetime import date, datetime, timezone

from core import config
from core.news import Article

NEWS_CSV = config.ROOT / "data" / "eval" / "backtest_news.csv"


class AsOfViolation(RuntimeError):
    """Data dated after the case's as_of date was about to reach an approach."""


def last_complete_quarter(as_of):
    """The latest quarter that has ended on or before as_of: 2022-08-02 -> '2022-Q2'; 2022-09-30 -> '2022-Q3'."""
    for q in (4, 3, 2, 1):
        month = 3 * q
        end = date(as_of.year + (month == 12), month % 12 + 1, 1) - date.resolution  # last day of the quarter
        if end <= as_of:
            return f"{as_of.year}-Q{q}"
    return f"{as_of.year - 1}-Q4"


def as_of_datetime(as_of):
    """End of the as_of day, UTC - the 'now' the Evaluator uses for its freshness rule."""
    return datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=timezone.utc)


def guard_articles(articles, as_of):
    for a in articles:
        if datetime.fromisoformat(a.published).date() > as_of:
            raise AsOfViolation(f"article dated {a.published[:10]} is after as_of {as_of}: {a.title[:60]}")
    return articles


def guard_fx_end(end, as_of):
    if end is None or end > as_of:
        raise AsOfViolation(f"FX window may not end after as_of {as_of} (asked for {end})")


def check_sql(sql, as_of):
    """A query on the purchase table must name a quarter, and no named quarter may be later than as_of's last complete one."""
    if "purchases" not in sql.lower():
        return
    quarters = re.findall(r"'(\d{4}-Q[1-4])'", sql)
    if not quarters:
        raise AsOfViolation("a query on ganga.purchases without a quarter limit is not allowed in a backtest")
    cap = last_complete_quarter(as_of)
    late = [q for q in quarters if q > cap]
    if late:
        raise AsOfViolation(f"query names quarter {late[0]}, after the last complete quarter {cap} before as_of {as_of}")


class AsOfZetaris:
    """A Zetaris client that refuses any purchase-table query that could read past as_of."""

    def __init__(self, zt, as_of):
        self._zt, self.as_of = zt, as_of
        self.max_quarter = last_complete_quarter(as_of)

    def query(self, sql, *args, **kwargs):
        check_sql(sql, self.as_of)
        return self._zt.query(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._zt, name)


def load_news(case_id, as_of, path=None):
    """Headlines for one case from the prepared file, newest first. Anything dated after as_of is a violation, not silently dropped."""
    out = []
    with open(path or NEWS_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["case_id"] != case_id:
                continue
            out.append(Article(title=r["title"], publisher=r["publisher"], url=r["url"],
                               published=f"{r['published']}T00:00:00+00:00", summary="", feed="prepared-backtest-news"))
    guard_articles(out, as_of)
    return sorted(out, key=lambda a: a.published, reverse=True)
