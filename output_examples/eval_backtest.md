# Backtest: Vidhur vs simpler approaches - NOT COMPLETED

**Status: the backtest did not produce a comparison.** 5 cases were planned, one run each. B1 and B2 ran but both model-based approaches
ended "not completed" because the Nightshift 1.2.1 runtime answered 502 on every retry. B3 was in progress when the run was stopped by the
operator (time limit); B4 and B5 were never run. **No claim is made about whether Vidhur beats the naive or model-only approach.**

Company data is synthetic (Ganga AI Solutions); market data (exchange rates) is real and historical; news is a small prepared set of real dated
headlines (`data/eval/backtest_news.csv`, prepared evaluation data). 5 cases, one run, a small sample.

## What was measured

| Case | as_of | Real outcome (91 days later) | Naive (always flat) | Model only (one Nemotron call) | Vidhur |
|---|---|---|---|---|---|
| B1 chip shortage, early 2021 | 2021-02-01 | flat (-1.56 %, £-14,193) | flat (right) | not completed (502) | not completed (502) |
| B2 Taiwan Strait tension | 2022-08-02 | up (+3.06 %, £+55,519) | flat (wrong) | not completed (502) | not completed (502, at the planning stage) |
| B3 UK mini-budget | 2022-09-23 | not computed | - | not run | interrupted, not scored |
| B4 calmer quarter | 2023-05-02 | not computed | - | not run | not run |
| B5 calmer quarter | 2024-09-02 | not computed | - | not run | not run |

Real outcomes are computed in Python from real ECB rates (Frankfurter) and Ganga's synthetic purchase mix for the last quarter completed before
`as_of`, assuming 100 % pass-through of the supplier's currency; up if > +2 %, down if < -2 %, otherwise flat. **Taiwan dollar is proxied by USD
because the ECB does not publish TWD.** The naive column is only a baseline on two cases and says nothing about the other approaches.

Per-currency moves behind the outcomes: B1 USD -1.22 %, EUR -1.55 %, CNY -1.32 %, KRW -1.60 %, MYR -2.70 %, JPY -5.37 %.
B2 USD +5.73 %, EUR +2.86 %, CNY -1.48 %, JPY -5.89 %, KRW -1.93 %, MYR -0.61 % (positive = the currency became dearer in pounds).

## What happened to the model calls

- B1 model-only: 0 successful calls, 4 failed attempts (HTTP 502). B1 Vidhur: 7 successful calls, 4 failed attempts (502); the Currency Analyst work item D4 (P002) was not assessed.
- B2 model-only: 0 successful, 4 failed (502, routed to `nvidia/nemotron-3.5-lightning-30b-a3b`, `empty_response`). B2 Vidhur: 0 successful, 4 failed (502); the Planner could not run.
- B3 Vidhur (interrupted): 1 successful call, 2 failed attempts (502) before it was stopped.
- Retry policy: 4 attempts per call (one try plus 3 retries), 60 s apart. Receipt ids are not exposed by Nightshift 1.2.1 in these responses.
- Failures are reported as "not completed", never as right or wrong.

## Why it stopped

The runtime was returning 502 on most calls (`provider_error`: `empty_response` and `provider_deadline`), each case needs dozens of model calls with 60 s waits
between retries, and the job hit its time limit. The first attempt also hit two intermittent Zetaris HTTP 400 errors between cases; a retry in the Zetaris client was added and tested.

## What is ready

`scripts/eval_backtest.py` is built and unit tested (`tests/test_eval_scripts.py`): outcome maths, the three approaches, scoring that keeps "not completed" apart,
and the point-in-time guards in `core/asof.py` that raise if any news, FX window or Zetaris quarter after `as_of` would reach an approach. It can resume with
`python scripts/eval_backtest.py --previous logs/backtest_results_20261023.jsonl --only B3,B4,B5` when the runtime is healthy.
Traces: `logs/backtest_B1_*.jsonl`, `logs/backtest_B2_*.jsonl`, `logs/backtest_B3_vidhur_20261023_232659.jsonl`; results file `logs/backtest_results_20261023.jsonl`.
The model may remember these historical events from training; that cannot be removed.
