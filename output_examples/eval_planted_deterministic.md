# Planted-claim test: deterministic checks only (no AI)

Vidhur's Evaluator (professional scepticism) runs exact, rule-based checks before it asks a model anything. This run uses ONLY those checks: no Nightshift, no model call. The 25 original planted claims (`data/eval/planted_claims.csv`) are unedited; 6 NEW claims (`data/eval/planted_claims_new.csv`, one per new check) are reported separately.

- Run: 2026-10-24T02:46:11+00:00 | results: `logs/eval_planted_det_20261024_081613.jsonl` | purchase rows: `data/purchases.csv` (2026-Q3) | USD/GBP for the baseline: 1 GBP = 1.3368 USD on 2026-10-23 (ECB)
- Articles are prepared evaluation data (`data/eval/planted_sources*.csv`, synthetic - not real news). ECB rates are live (Frankfurter).
- Small sample written by the builder: it shows which flaws each check catches, not a general accuracy figure.

## Result

- **Original 25 claims, deterministic checks only: 17/20 false claims caught** (the AI-assisted run of 2026-10-23 caught 15/20 of the same 20 - see the table; that run also used the model check).
- **Correct control claims wrongly rejected: 0/5** (correctly passed: 5; not evaluated: 0).
- **6 NEW claims: 6/6 caught** (6 by their intended check).

## Original 20 false claims, by failure type (old = AI-assisted run, new = deterministic checks only)

| Failure type | Planted | Old: caught | Old: missed | Old: not evaluated | New: caught | New: caught by the intended check | New: missed | New: not evaluated |
|---|---|---|---|---|---|---|---|---|
| fabricated number | 1 | 0 | 1 | 0 | 1 | 1 | 0 | 0 |
| FX mismatch | 2 | 2 | 0 | 0 | 2 | 2 | 0 | 0 |
| wrong currency | 1 | 0 | 1 | 0 | 1 | 1 | 0 | 0 |
| GBP mismatch | 2 | 0 | 2 | 0 | 2 | 2 | 0 | 0 |
| duplicate source | 2 | 2 | 0 | 0 | 2 | 2 | 0 | 0 |
| stale source | 2 | 2 | 0 | 0 | 2 | 2 | 0 | 0 |
| other | 10 | 9 | 0 | 1 | 7 | 0 | 3 | 0 |

"Caught by the intended check" = the send-back reasons include the check built for that failure type (fabricated number: NUMBER_NOT_IN_SOURCE or FX_MISMATCH; FX: FX_MISMATCH; wrong currency: CURRENCY_COUNTRY_MISMATCH; GBP: GBP_MISMATCH; duplicate: DUPLICATE_SOURCE; stale: STALE_SOURCE). A claim can be caught by an unrelated check and still count as caught. 'other' (cause not supported, wrong date, untrusted publisher, single source, wrong country) has no dedicated number check.

## The 6 NEW planted claims (one per new check)

| Case | Failure type | Planted flaw | Outcome | Send-back reason code(s) |
|---|---|---|---|---|
| N01 (NEW) | fabricated number | NEW. The claim quotes a 23 percent rise that neither cited article prints | CAUGHT | NUMBER_NOT_IN_SOURCE |
| N02 (NEW) | FX mismatch | NEW. The claimed GBP/KRW end rate is 8 percent away from the real ECB rate for that date | CAUGHT | FX_MISMATCH, FX_MISMATCH |
| N03 (NEW) | wrong currency | NEW. A German part is explained with GBP/KRW data (won is South Korea's currency) | CAUGHT | CURRENCY_COUNTRY_MISMATCH |
| N04 (NEW) | GBP mismatch | NEW. The claim states a GBP exposure about 3x Ganga's real quarterly spend on P003 | CAUGHT | GBP_MISMATCH |
| N05 (NEW) | duplicate source | NEW. Two outlets run the same report: near-identical excerpts and a via-Bloomberg credit with different headlines | CAUGHT | DUPLICATE_SOURCE, SOURCES_NOT_INDEPENDENT |
| N06 (NEW) | stale source | NEW. Both cited sources are just outside the 30-day freshness limit (33 and 41 days old) | CAUGHT | STALE_SOURCE, STALE_SOURCE, STALE_SOURCES, NO_TRUSTED_SOURCE |

## Every original case

| Case | Failure type | Planted flaw | Old (AI-assisted) | New (deterministic) | New: reason code(s) |
|---|---|---|---|---|---|
| F01 | FX mismatch | The % range is 3x the move computed from the ECB data | CAUGHT | CAUGHT | FX_MISMATCH |
| F02 | FX mismatch | The direction is the opposite of what the ECB data shows | CAUGHT | CAUGHT | FX_MISMATCH |
| F03 | fabricated number | The computed FX moves quoted in the claim are invented (the cited ECB article shows different numbers) | MISSED | CAUGHT | NUMBER_NOT_IN_SOURCE, FX_MISMATCH |
| F04 | wrong currency | A South Korean part is explained with GBP/JPY data (yen is Japan's currency) | MISSED | CAUGHT | CURRENCY_COUNTRY_MISMATCH |
| F05 | stale source | One of two cited sources is 75 days old (limit 30) | CAUGHT | CAUGHT | STALE_SOURCE, DUPLICATE_SOURCE, STALE_SOURCES |
| F06 | stale source | Both cited sources are 75-80 days old (limit 30) | CAUGHT | CAUGHT | STALE_SOURCE, STALE_SOURCE, STALE_SOURCES, NO_TRUSTED_SOURCE |
| F07 | other | Both sources are unlisted blogs (Tier 3) | CAUGHT | CAUGHT | NO_TRUSTED_SOURCE |
| F08 | other | One unlisted blog cited twice with a second unlisted rumour site | CAUGHT | CAUGHT | NO_TRUSTED_SOURCE |
| F09 | other | Only one source cited | CAUGHT | CAUGHT | TOO_FEW_SOURCES |
| F10 | other | The same source URL cited twice (counts once) | CAUGHT | CAUGHT | TOO_FEW_SOURCES |
| F11 | duplicate source | Two publishers carrying the identical syndicated headline | CAUGHT | CAUGHT | DUPLICATE_SOURCE, SOURCES_NOT_INDEPENDENT |
| F12 | duplicate source | Two articles from the same publisher on the same story | CAUGHT | CAUGHT | DUPLICATE_SOURCE, SOURCES_NOT_INDEPENDENT |
| F13 | other | Ganga buys the GA100 GPU chip (P001) from Taiwan not Japan | CAUGHT | CAUGHT | COUNTRY_MISMATCH |
| F14 | other | Ganga buys the server PCB (P004) from China not Taiwan | CAUGHT | CAUGHT | COUNTRY_MISMATCH |
| F15 | GBP mismatch | The claim states a GBP exposure about 10x Ganga's real quarterly spend on P001 (Zetaris) | MISSED | CAUGHT | NUMBER_NOT_IN_SOURCE, GBP_MISMATCH |
| F16 | GBP mismatch | The claim states a GBP exposure about 10x too small versus Ganga's real quarterly spend on P002 (Zetaris) | MISSED | CAUGHT | NUMBER_NOT_IN_SOURCE, GBP_MISMATCH |
| F17 | other | Cited sources report GPU prices falling on a glut; the claim says export rules push them up | CAUGHT | MISSED | - |
| F18 | other | Cited sources describe subsidies and say no tariffs were announced; the claim blames a 25 percent tariff | CAUGHT | CAUGHT | NUMBER_NOT_IN_SOURCE |
| F19 | other | The claim dates the Taiwan licensing rule to March 2025; the cited articles (5-7 days old) report it as new this week | NOT_EVALUATED | MISSED | - |
| F20 | other | The claim says the rule takes effect on a date in the past that contradicts the articles | CAUGHT | MISSED | - |
| C01 | control | none - well supported (Taiwan GPU export licensing) | CORRECTLY_PASSED | CORRECTLY_PASSED | - |
| C02 | control | none - well supported (Korea HBM export rules) | CORRECTLY_PASSED | CORRECTLY_PASSED | - |
| C03 | control | none - well supported (Japan substrate material export rule) | CORRECTLY_PASSED | CORRECTLY_PASSED | - |
| C04 | control | none - well supported (China gallium licensing) | CORRECTLY_PASSED | CORRECTLY_PASSED | - |
| C05 | control | none - currency claim whose numbers match the ECB data | CORRECTLY_PASSED | CORRECTLY_PASSED | - |

## Notes and limits

- The test cases were not edited to improve results. The only harness change: the old code `NOT_INDEPENDENT` (copied reports) was renamed `DUPLICATE_SOURCE`, and the expected code in the unedited CSV is mapped to it.
- Claims whose flaw is about meaning (F17 prices fell, F19/F20 wrong dates) need the model check; with no AI they are missed. That is the price of running offline and is why the model check still exists.
- Controls C01-C04 carry an analyst percentage range with no source label (the old format); the number check only inspects figures written in the claim text and ranges explicitly labelled FORECAST.
- FX checks use live ECB data: re-running on another day uses that day's rates (the planted flaw is relative to the real rate, so results hold).
