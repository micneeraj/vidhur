# Ganga Unified Semantic Layer with audit rules (live-tested 2026-10-31, local Zetaris)

Original tables (`ganga.purchases`, `ganga.parts`, `ganga.suppliers`) are never changed. The bad row lives in its own
view, `ganga_live.purchases_planted_bad_row`, labelled `PLANTED DEMO ROW - not real data`.

Run each block as ONE statement (SQL Workspace or REST). `COMPILE USL` must be sent whole, `;`s included.

## 1. Planted bad row (separate demo table)
```sql
CREATE SCHEMASTORE VIEW purchases_planted_bad_row WITH CONTAINER ganga_live AS
SELECT '2026-Q3' AS quarter, 'P999' AS part_id, 10 AS quantity, 'JPY' AS currency, -5.0 AS unit_price,
       'PLANTED DEMO ROW - not real data' AS row_label
```

## 2. Namespace and model
```sql
CREATE NAMESPACE IF NOT EXISTS lightning.metastore
CREATE NAMESPACE IF NOT EXISTS lightning.metastore.ganga_demo

COMPILE USL IF NOT EXISTS ganga_usl DEPLOY NAMESPACE lightning.metastore.ganga_demo DDL
CREATE TABLE supplier (supplier_id varchar(10) NOT NULL PRIMARY KEY, supplier_name varchar(200), country varchar(50));
CREATE TABLE part (part_id varchar(10) NOT NULL PRIMARY KEY, part_name varchar(200), category varchar(100), supplier_id varchar(10) FOREIGN KEY REFERENCES supplier(supplier_id));
CREATE TABLE fx_rate (currency varchar(3) NOT NULL PRIMARY KEY, rate_per_gbp double, rate_date date);
CREATE TABLE purchase (quarter varchar(10), part_id varchar(10), supplier_id varchar(10), supplier_country varchar(50), quantity double, currency varchar(3), unit_price double, row_label varchar(100));
```

## 3. Map to the real tables (ACTIVATE)
Gotcha found live: a `NOT NULL PRIMARY KEY` column must come from a non-nullable expression, otherwise
"Schema mismatch". `COALESCE(col, '')` fixes it.
```sql
ACTIVATE USL TABLE lightning.metastore.ganga_demo.ganga_usl.supplier AS
SELECT COALESCE(supplier_id, '') AS supplier_id, supplier_name, country FROM ganga.suppliers

ACTIVATE USL TABLE lightning.metastore.ganga_demo.ganga_usl.part AS
SELECT COALESCE(part_id, '') AS part_id, part_name, category, supplier_id FROM ganga.parts

ACTIVATE USL TABLE lightning.metastore.ganga_demo.ganga_usl.fx_rate AS
SELECT currency, CAST(rate_per_gbp AS DOUBLE) AS rate_per_gbp, rate_date FROM ganga_live.fx_live

ACTIVATE USL TABLE lightning.metastore.ganga_demo.ganga_usl.purchase AS
SELECT u.quarter, u.part_id, s.supplier_id, s.country AS supplier_country, CAST(u.quantity AS DOUBLE) AS quantity,
       u.currency, CAST(u.unit_price AS DOUBLE) AS unit_price, u.row_label
FROM (SELECT quarter, part_id, quantity, currency, unit_price, 'real' AS row_label FROM ganga.purchases
      UNION ALL
      SELECT quarter, part_id, quantity, currency, unit_price, row_label FROM ganga_live.purchases_planted_bad_row) u
LEFT JOIN ganga.parts p ON u.part_id = p.part_id
LEFT JOIN ganga.suppliers s ON p.supplier_id = s.supplier_id
```

## 4. The three audit rules
USD is accepted for non-euro suppliers (Taiwan's TWD is not published by the ECB; Ganga's chip suppliers invoice in USD - see README).
```sql
REGISTER DQ currency_matches_supplier_country TABLE lightning.metastore.ganga_demo.ganga_usl.purchase AS
(supplier_country = 'Taiwan' AND currency IN ('TWD','USD')) OR (supplier_country = 'South Korea' AND currency IN ('KRW','USD'))
OR (supplier_country = 'Japan' AND currency IN ('JPY','USD')) OR (supplier_country = 'China' AND currency IN ('CNY','USD'))
OR (supplier_country = 'Germany' AND currency = 'EUR') OR (supplier_country = 'Malaysia' AND currency IN ('MYR','USD'))

REGISTER DQ price_positive TABLE lightning.metastore.ganga_demo.ganga_usl.purchase AS unit_price > 0

REGISTER DQ known_supplier TABLE lightning.metastore.ganga_demo.ganga_usl.purchase AS supplier_id IS NOT NULL
```

## 5. Run them (demo clip C5)
```sql
LIST DQ USL lightning.metastore.ganga_demo.ganga_usl
RUN DQ price_positive TABLE lightning.metastore.ganga_demo.ganga_usl.purchase
SHOW DQ INVALID RECORD price_positive TABLE lightning.metastore.ganga_demo.ganga_usl.purchase
```
Result on 2026-10-31: each rule 249 total, 248 valid, 1 invalid. The invalid record is the planted row
(P999, JPY, unit_price -5.0, no supplier). All 248 real purchase rows pass all three rules.
