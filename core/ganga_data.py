"""Reads Ganga's purchase data from Zetaris (never from the CSV files)."""

from dataclasses import dataclass


@dataclass
class Target:
    part_id: str
    part_name: str
    category: str
    country: str
    supplier_name: str
    quarter: str
    quarterly_spend_gbp: float


def latest_quarter(zt, max_quarter=None):
    """The newest quarter in the purchase data. max_quarter ("2022-Q2") caps it - used by the backtest so nothing after its as_of date is read."""
    cap = f" WHERE quarter <= '{max_quarter}'" if max_quarter else ""
    return zt.query(f"SELECT MAX(quarter) AS q FROM ganga.purchases{cap}")[0]["q"]


def top_targets(zt, n=3, max_quarter=None):
    """Top-n (part, supplier country) pairs by spend in the latest quarter (or the latest one on or before max_quarter)."""
    quarter = latest_quarter(zt, max_quarter)
    rows = zt.query(
        "SELECT p.part_id AS part_id, p.part_name AS part_name, p.category AS category, "
        "s.country AS country, s.supplier_name AS supplier_name, u.quarter AS quarter, u.total_gbp AS spend "
        "FROM ganga.purchases u "
        "JOIN ganga.parts p ON u.part_id = p.part_id "
        "JOIN ganga.suppliers s ON p.supplier_id = s.supplier_id "
        f"WHERE u.quarter = '{quarter}' ORDER BY u.total_gbp DESC"
    )
    return [
        Target(r["part_id"], r["part_name"], r["category"], r["country"], r["supplier_name"], r["quarter"], float(r["spend"]))
        for r in rows[:n]
    ]


def part_country_catalog(zt):
    """{part_id: {"part_name", "country"}} for every part Ganga buys - used by the Evaluator."""
    rows = zt.query(
        "SELECT p.part_id AS part_id, p.part_name AS part_name, s.country AS country "
        "FROM ganga.parts p JOIN ganga.suppliers s ON p.supplier_id = s.supplier_id"
    )
    return {r["part_id"]: {"part_name": r["part_name"], "country": r["country"]} for r in rows}


def quarterly_spend(zt, part_id, quarter=None):
    """Spend (GBP) on one part in one quarter (default: latest), and the quarter used."""
    quarter = quarter or latest_quarter(zt)
    safe_part = part_id.replace("'", "")
    rows = zt.query(f"SELECT SUM(total_gbp) AS spend FROM ganga.purchases WHERE part_id = '{safe_part}' AND quarter = '{quarter}'")
    value = rows[0]["spend"] if rows else None
    if value is None:
        raise LookupError(f"No spend for {part_id} in {quarter}")
    return float(value), quarter
