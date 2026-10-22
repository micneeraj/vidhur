"""Register Ganga's synthetic CSVs (data/*.csv) as tables in Zetaris. Safe to run twice.

What it does:
  1. Copies each CSV into the Zetaris driver container's customer-data volume
     (docker cp - nothing inside ~/zetaris-platform is touched).
  2. Creates the `ganga` Lightning database if it is missing.
  3. Creates a FILESTORE table per CSV if it is missing. If a table exists but its
     row count differs from the CSV, it is dropped and re-registered.
  4. Prints a verification query run through Zetaris.

Usage:  python scripts/load_ganga_to_zetaris.py
"""

import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import config  # noqa: E402
from core.zetaris_client import ZetarisClient  # noqa: E402

DATABASE = "ganga"
DESCRIPTION = "Ganga AI Solutions synthetic purchases"
VOLUME_DIR = "/home/zetaris/customer-data/shared/ganga"
DATA_DIR = ROOT / "data"


def csv_row_count(path):
    with open(path, newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.reader(f)) - 1  # minus header


def copy_into_zetaris_volume(csv_files):
    container = config.env("ZETARIS_DRIVER_CONTAINER", "zetaris-driver-1")
    subprocess.run(["docker", "exec", "-u", "root", container, "mkdir", "-p", VOLUME_DIR], check=True, capture_output=True)
    for path in csv_files:
        subprocess.run(["docker", "cp", str(path), f"{container}:{VOLUME_DIR}/{path.name}"], check=True, capture_output=True)
        print(f"  copied {path.name} -> {container}:{VOLUME_DIR}/")
    subprocess.run(["docker", "exec", "-u", "root", container, "chmod", "-R", "a+rX", VOLUME_DIR], check=True, capture_output=True)


def create_table_sql(table, database=DATABASE):
    return (
        f"CREATE LIGHTNING FILESTORE TABLE {table} FROM {database} FORMAT CSV OPTIONS ("
        f'path "file://{VOLUME_DIR}/{table}.csv", inferSchema "true", header "true")'
    )


def main():
    csv_files = sorted(DATA_DIR.glob("*.csv"))
    if not csv_files:
        sys.exit("No CSV files found in data/")

    zt = ZetarisClient()
    if not zt.health_check():
        sys.exit("Zetaris health check failed (SELECT 1).")
    print("Zetaris reachable.")

    print("Copying CSVs into the Zetaris volume:")
    copy_into_zetaris_volume(csv_files)

    databases = {row["name"] for row in zt.query("SHOW LIGHTNING DATABASES")}
    if DATABASE not in databases:
        zt.query(f'CREATE LIGHTNING DATABASE {DATABASE} DESCRIBE BY "{DESCRIPTION}"')
        print(f"Created database {DATABASE}.")

    existing = {row["table_identifier"].split(".")[-1] for row in zt.query(f"SHOW TABLES IN {DATABASE}")}
    for path in csv_files:
        table = path.stem
        expected = csv_row_count(path)
        if table in existing:
            actual = int(zt.query(f"SELECT COUNT(*) AS n FROM {DATABASE}.{table}")[0]["n"])
            if actual == expected:
                print(f"  {DATABASE}.{table}: already loaded ({actual} rows) - nothing to do")
                continue
            print(f"  {DATABASE}.{table}: {actual} rows in Zetaris but {expected} in CSV - re-registering")
            zt.query(f"DROP LIGHTNING DATABASE TABLE {DATABASE}.{table}")
        zt.query(create_table_sql(table))
        actual = int(zt.query(f"SELECT COUNT(*) AS n FROM {DATABASE}.{table}")[0]["n"])
        status = "OK" if actual == expected else "ROW COUNT MISMATCH"
        print(f"  {DATABASE}.{table}: registered, {actual} rows ({status})")
        if actual != expected:
            sys.exit(1)

    print("\nProof query - total spend (GBP) by supplier country and part, all quarters:")
    rows = zt.query(spend_by_country_and_part_sql())
    print(f"{'country':<12} {'part':<34} {'total_spend_gbp':>16}")
    for r in rows:
        print(f"{r['country']:<12} {r['part_name']:<34} {float(r['total_spend_gbp']):>16,.2f}")
    print("\n(Synthetic data - Ganga AI Solutions is fictional.)")


def spend_by_country_and_part_sql():
    return (
        "SELECT s.country AS country, p.part_name AS part_name, ROUND(SUM(u.total_gbp), 2) AS total_spend_gbp "
        f"FROM {DATABASE}.purchases u "
        f"JOIN {DATABASE}.parts p ON u.part_id = p.part_id "
        f"JOIN {DATABASE}.suppliers s ON p.supplier_id = s.supplier_id "
        "GROUP BY s.country, p.part_name ORDER BY total_spend_gbp DESC"
    )


if __name__ == "__main__":
    main()
