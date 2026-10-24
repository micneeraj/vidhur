# Live 3-driver run d9decb7d (24 Oct 2026, 21:26 IST): all three drivers verified, two challenges unfinished

A real run in demo mode (`--demo`: three drivers, evidence cached on 2026-10-24, FX rates live, with `--demo-weak-claim` and `--demo-red-team` on). Run status: **partial**. 1502 s, 15 model calls ok, 9 failed attempts (4 `InternalServerError` 502, 5 `APITimeoutError`). The Nightshift runtime was overloaded, so some calls were retried and some gave up.

## What was verified

| Driver | Claim | Evaluator | Confidence | Verified change in quarterly cost (worst / likely / best) |
|---|---|---|---|---|
| D1 Taiwan GPU chip (geopolitics) | Taiwan's export controls on AI chips raise supplier compliance costs | Sent back once (`TOO_FEW_SOURCES`: 1 source cited, 2 required), then **verified** | Low | +£160,978 / +£80,489 / +£26,830 |
| D2 EUR-priced German cooling (currency) | The pound strengthened against the euro | **Verified**, no send-back | High | -£8,967 / -£10,446 / -£11,926 |
| D3 South Korea HBM memory (supplier & commodity) | HBM taking about 30% of DRAM capacity may tighten supply and lift prices | **Verified**, no send-back | Medium | +£39,512 / +£19,756 / +£6,585 |

## What did not finish

- **D1 first proposal:** the demo flag asked for an aggressive hedge, but the model's reply was unusable, so the Decision Maker used its documented fallback, **"no action, monitor"** (trace step `propose:fallback`).
- **D1 Red Team:** raised **3 objections** (2 high: cheaper alternative, concentration; 1 medium: weak evidence). The Decision Maker's response then failed (one 502, two timeouts), so **the challenge is unresolved**. The brief in this folder was written before the label fix and shows D1 as "Proposed, UNREVIEWED"; the evidence trail here shows the real count and "challenge unresolved".
- **D3 Red Team:** the challenge call timed out and then hit the run time budget (`RunBudgetExceeded`), so no objections were raised. D3's lock-the-price proposal is **unreviewed**.
- **D2 Red Team:** worked. 2 objections (1 medium: cheaper alternative; 1 low: weak evidence); the Decision Maker defended both; final decision no action, monitor.

## Files

- `brief_20261024_215151.md`: the CFO brief, copied as the run wrote it (it predates the unresolved-challenge label).
- `run_20261024_212648.jsonl`: the trace of every step. `world_state_d9decb7d.json`: the run's shared memory.
- `evidence_trail.html`: **re-rendered, not the original file.** It was rebuilt from `world_state_d9decb7d.json` with the current page code (no model calls), so D1 shows the real objection count and "challenge unresolved" instead of the old "not challenged, 0 objections" label.
