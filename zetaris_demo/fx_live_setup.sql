-- Live ECB rates as a Zetaris REST source (same pattern as getting-started PokeAPI example).
-- Run one statement at a time. Live-tested 2026-10-31 on local Zetaris.
CREATE LIGHTNING DATABASE FX_REST DESCRIBE BY "ECB reference rates via Frankfurter REST API";

CREATE LIGHTNING REST TABLE ecb_gbp_latest FROM FX_REST REQUEST(
    endpoint "https://api.frankfurter.dev/v1/latest?base=GBP&symbols=USD,EUR,KRW,JPY,CNY,MYR",
    method "get", response_type "json", http_encoding "URLENCODED"
) HEADER () BODY ();

-- ganga is a filestore Lightning database, so views live in a schemastore container: ganga_live.
CREATE SCHEMASTORE CONTAINER ganga_live;

-- One row per currency, units per 1 GBP. TWD is not published by the ECB: proxied by USD (as in README / core/fx.py).
CREATE SCHEMASTORE VIEW fx_live WITH CONTAINER ganga_live AS
SELECT c.currency, c.rate_per_gbp, CAST(date AS DATE) AS rate_date, c.note
FROM fx_rest.ecb_gbp_latest
LATERAL VIEW inline(array(
  named_struct('currency','USD','rate_per_gbp',rates.USD,'note','ECB'),
  named_struct('currency','EUR','rate_per_gbp',rates.EUR,'note','ECB'),
  named_struct('currency','KRW','rate_per_gbp',rates.KRW,'note','ECB'),
  named_struct('currency','JPY','rate_per_gbp',rates.JPY,'note','ECB'),
  named_struct('currency','CNY','rate_per_gbp',rates.CNY,'note','ECB'),
  named_struct('currency','MYR','rate_per_gbp',rates.MYR,'note','ECB'),
  named_struct('currency','TWD','rate_per_gbp',rates.USD,'note','USD proxy: ECB does not publish TWD'))) c;
