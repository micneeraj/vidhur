# Planted false-claim test of the Evaluator

Vidhur's Evaluator is its professional-scepticism layer: the auditor's way of not accepting a claim without evidence. This test plants 20 false or weak claims (and 5 correct controls) and checks whether the Evaluator sends them back.

- Run: 2026-10-23T22:02:00 (local time) | trace: `logs/run_20261023_220200.jsonl` | results: `logs/eval_planted_20261023_220200.jsonl`
- Evaluator model lane: `vidhur-analyst` | retry policy: 4 attempts per call, 60 s apart
- Claims: `data/eval/planted_claims.csv`. Articles: `data/eval/planted_sources.csv` - **prepared evaluation data (synthetic articles, not real news)**. Ganga's data comes from Zetaris (synthetic company); the ECB exchange rates are live.
- One run, 25 claims, a small sample. Same Evaluator code path as the product; no mocks, no response caching.

## Result

- **False claims caught: 15/20** (missed: 4; could not be evaluated: 1)
- **Correct claims wrongly rejected: 0/5** (correctly passed: 5; could not be evaluated: 0)
- **Could not be evaluated because of runtime errors: 1** (counted separately, never as caught)
- Of the 15 caught, 12 were caught for the planted reason code (claims without an expected code in the CSV are not counted there).
- Among the claims that could be evaluated, 15/19 false claims were caught.

## By category (false claims)

| Category | Planted | Caught | Missed | Not evaluated |
|---|---|---|---|---|
| wrong_fx_number | 3 | 2 | 1 | 0 |
| wrong_currency_pair | 1 | 0 | 1 | 0 |
| stale_source | 2 | 2 | 0 | 0 |
| untrusted_publisher_only | 2 | 2 | 0 | 0 |
| single_source | 2 | 2 | 0 | 0 |
| copied_report | 2 | 2 | 0 | 0 |
| wrong_supplier_country | 2 | 2 | 0 | 0 |
| gbp_impact_mismatch | 2 | 0 | 2 | 0 |
| cause_not_supported | 2 | 2 | 0 | 0 |
| wrong_date | 2 | 1 | 0 | 1 |

## Every case

| Case | Planted flaw | Outcome | Send-back reason code(s) | Model that answered | Routed model / failure detail (on a failed call) | Receipt id |
|---|---|---|---|---|---|---|
| F01 | The % range is 3x the move computed from the ECB data | CAUGHT | FX_MISMATCH | - | - | not exposed by Nightshift 1.2.1 |
| F02 | The direction is the opposite of what the ECB data shows | CAUGHT | FX_MISMATCH | - | - | not exposed by Nightshift 1.2.1 |
| F03 | The computed FX moves quoted in the claim are invented (the cited ECB article shows different numbers) | MISSED | - | nvidia/nemotron-3.5-lightning-30b-a3b | - | not exposed by Nightshift 1.2.1 |
| F04 | A South Korean part is explained with GBP/JPY data (yen is Japan's currency) | MISSED | - | nvidia/nemotron-3.5-lightning-30b-a3b | provider_deadline | not exposed by Nightshift 1.2.1 |
| F05 | One of two cited sources is 75 days old (limit 30) | CAUGHT | STALE_SOURCE, NOT_INDEPENDENT, STALE_SOURCES | - | - | not exposed by Nightshift 1.2.1 |
| F06 | Both cited sources are 75-80 days old (limit 30) | CAUGHT | STALE_SOURCE, STALE_SOURCE, STALE_SOURCES, NO_TRUSTED_SOURCE | - | - | not exposed by Nightshift 1.2.1 |
| F07 | Both sources are unlisted blogs (Tier 3) | CAUGHT | NO_TRUSTED_SOURCE | - | - | not exposed by Nightshift 1.2.1 |
| F08 | One unlisted blog cited twice with a second unlisted rumour site | CAUGHT | NO_TRUSTED_SOURCE | - | - | not exposed by Nightshift 1.2.1 |
| F09 | Only one source cited | CAUGHT | TOO_FEW_SOURCES | - | - | not exposed by Nightshift 1.2.1 |
| F10 | The same source URL cited twice (counts once) | CAUGHT | TOO_FEW_SOURCES | - | - | not exposed by Nightshift 1.2.1 |
| F11 | Two publishers carrying the identical syndicated headline | CAUGHT | NOT_INDEPENDENT, SOURCES_NOT_INDEPENDENT | - | - | not exposed by Nightshift 1.2.1 |
| F12 | Two articles from the same publisher on the same story | CAUGHT | NOT_INDEPENDENT, SOURCES_NOT_INDEPENDENT | - | - | not exposed by Nightshift 1.2.1 |
| F13 | Ganga buys the GA100 GPU chip (P001) from Taiwan not Japan | CAUGHT | COUNTRY_MISMATCH | - | - | not exposed by Nightshift 1.2.1 |
| F14 | Ganga buys the server PCB (P004) from China not Taiwan | CAUGHT | COUNTRY_MISMATCH | - | - | not exposed by Nightshift 1.2.1 |
| F15 | The claim states a GBP exposure about 10x Ganga's real quarterly spend on P001 (Zetaris) | MISSED | - | nvidia/nemotron-3.5-lightning-30b-a3b | - | not exposed by Nightshift 1.2.1 |
| F16 | The claim states a GBP exposure about 10x too small versus Ganga's real quarterly spend on P002 (Zetaris) | MISSED | - | nvidia/nemotron-3.5-lightning-30b-a3b | - | not exposed by Nightshift 1.2.1 |
| F17 | Cited sources report GPU prices falling on a glut; the claim says export rules push them up | CAUGHT | NOT_SUPPORTED | nvidia/nemotron-3.5-lightning-30b-a3b | - | not exposed by Nightshift 1.2.1 |
| F18 | Cited sources describe subsidies and say no tariffs were announced; the claim blames a 25 percent tariff | CAUGHT | NOT_SUPPORTED | nvidia/nemotron-3.5-lightning-30b-a3b | - | not exposed by Nightshift 1.2.1 |
| F19 | The claim dates the Taiwan licensing rule to March 2025; the cited articles (5-7 days old) report it as new this week | NOT_EVALUATED | - | - | provider_deadline | not exposed by Nightshift 1.2.1 |
| F20 | The claim says the rule takes effect on a date in the past that contradicts the articles | CAUGHT | NOT_SUPPORTED | nvidia/nemotron-3.5-lightning-30b-a3b | - | not exposed by Nightshift 1.2.1 |
| C01 | none - well supported (Taiwan GPU export licensing) | CORRECTLY_PASSED | - | nvidia/nemotron-3.5-lightning-30b-a3b | - | not exposed by Nightshift 1.2.1 |
| C02 | none - well supported (Korea HBM export rules) | CORRECTLY_PASSED | - | nvidia/nemotron-3.5-lightning-30b-a3b | - | not exposed by Nightshift 1.2.1 |
| C03 | none - well supported (Japan substrate material export rule) | CORRECTLY_PASSED | - | nvidia/nemotron-3.5-lightning-30b-a3b | - | not exposed by Nightshift 1.2.1 |
| C04 | none - well supported (China gallium licensing) | CORRECTLY_PASSED | - | nvidia/nemotron-3.5-lightning-30b-a3b | - | not exposed by Nightshift 1.2.1 |
| C05 | none - currency claim whose numbers match the ECB data | CORRECTLY_PASSED | - | nvidia/nemotron-3.5-lightning-30b-a3b | - | not exposed by Nightshift 1.2.1 |

## Notes and limits

- The deterministic checks (part and country against Zetaris, source count, freshness, independence, trust tier, FX numbers against the attached ECB moves) run first; only claims that pass them reach the Nemotron support check.
- Outcomes marked MISSED are reported as measured. In particular the Evaluator does not compare a GBP figure written inside a claim with Ganga's purchase data (the GBP impact is computed from Zetaris in Python after the claim passes), and it trusts the FX moves attached to a currency claim rather than re-fetching them. Those are gaps in this version, not test errors.
- Nightshift 1.2.1 often returns 502; a failed model check is NOT_EVALUATED, never a catch. Each call retries 3 times, 60 s apart.
- Model-call failures during this run: 5 failed attempts, 12 successful calls.
- Sample size is 25 claims written by the builder; this shows what the Evaluator does and does not catch, not a general accuracy figure.
