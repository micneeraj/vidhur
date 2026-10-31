-- Ganga's GBP exposure at today's live ECB rate, latest quarter, top 5 by part and supplier country.
-- Federates ganga.purchases (Zetaris filestore) with ganga_live.fx_live (live Frankfurter / ECB REST source).
SELECT p.part_id, pt.part_name, s.country AS supplier_country, p.currency,
       f.rate_per_gbp, f.rate_date,
       ROUND(SUM(p.quantity * p.unit_price) / f.rate_per_gbp, 2) AS gbp_exposure_live
FROM ganga.purchases p
JOIN ganga.parts pt ON p.part_id = pt.part_id
JOIN ganga.suppliers s ON pt.supplier_id = s.supplier_id
JOIN ganga_live.fx_live f ON p.currency = f.currency
WHERE p.quarter = (SELECT MAX(quarter) FROM ganga.purchases)
GROUP BY p.part_id, pt.part_name, s.country, p.currency, f.rate_per_gbp, f.rate_date
ORDER BY gbp_exposure_live DESC
LIMIT 5
