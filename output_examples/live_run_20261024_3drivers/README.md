# Live 3-driver run d9decb7d (24 Oct 2026, 21:26 IST)

3-driver live run under an overloaded runtime: all three claims verified; two Red Team steps did not complete (pending review).

A real run in demo mode (`--demo`: three drivers, news cached on 2026-10-24, ECB FX rates fetched live, with `--demo-weak-claim` and `--demo-red-team` on). Run status: **partial**. 1502 s, 15 model calls ok, 9 failed attempts (4 `InternalServerError` 502, 5 `APITimeoutError`). The Nightshift runtime was overloaded, so some calls were retried and some gave up.

## What was verified

| Driver | Claim | Evaluator | Confidence | Verified change in quarterly cost (worst / likely / best) |
|---|---|---|---|---|
| D1 Taiwan GPU chip (geopolitics) | Taiwan's export controls on AI chips raise supplier compliance costs | Sent back once (`TOO_FEW_SOURCES`: 1 source cited, 2 required), then **verified** | Low | +£160,978 / +£80,489 / +£26,830 |
| D2 EUR-priced German cooling (currency) | The pound strengthened against the euro | **Verified**, no send-back | High | -£8,967 / -£10,446 / -£11,926 |
| D3 South Korea HBM memory (supplier & commodity) | HBM taking about 30% of DRAM capacity may tighten supply and lift prices | **Verified**, no send-back | Medium | +£39,512 / +£19,756 / +£6,585 |

## Decision status (cleared vs pending review)

| Driver | Status | Action |
|---|---|---|
| D1 | **DECISION PENDING REVIEW** | Provisional recommendation: No action, monitor. Why provisional: Red Team review did not complete within the runtime budget; not cleared for CFO action. |
| D2 | **DECISION CLEARED** | No action, monitor (Red Team loop completed, both objections defended). |
| D3 | **DECISION PENDING REVIEW** | Provisional recommendation: Lock the price with the supplier (100% of volume). Why provisional: Red Team review did not complete within the runtime budget; not cleared for CFO action. |

## What did not finish

- **D1 first proposal:** the demo flag asked for an aggressive hedge, but the model's reply was unusable, so the Decision Maker used its documented fallback, **"no action, monitor"** (trace step `propose:fallback`).
- **D1 Red Team:** Red Team raised 3 objections (2 high: cheaper alternative, concentration; 1 medium: weak evidence); the Decision Maker's response timed out, so the challenge is unresolved.
- **D3 Red Team:** D3 completed claim verification and the GBP scenario calculation; its Red Team step hit the runtime budget in the saved live run (the challenge call timed out, then `RunBudgetExceeded`), so no objections were raised and D3's lock-the-price proposal is **provisional, pending review** (not cleared). The Red Team logic is implemented and tested; a completed D3 Red Team run is next work.
- **D2 Red Team:** worked. 2 objections (1 medium: cheaper alternative; 1 low: weak evidence); the Decision Maker defended both; final decision no action, monitor.

## Planner and scenario band

- D3 is selected by materiality (Korean HBM memory, 13% of spend) because memory is a distinct cost mechanism - industry supply and wafer allocation - rather than a repeat of geopolitical or FX analysis. The three drivers are the three largest identified exposures, about 84% of Q3 spend.
- D1 and D3 used the default scenario band. 2-12% is Ganga's default SCENARIO band for a verified directional cost driver with no directly comparable, quantified forecast for this part and quarter. It is not a probability and is not part-specific.
- Memory exposure is driven by industry capacity allocation and HBM supply tightness, separate from the Taiwan export-control and FX drivers.

## Price lock (re-rendered view)

The run's Decision Maker proposed locking the price for D3 and its saved rationale quotes a premium of £6,585. That figure was the 2.0% lock-premium assumption times D3's £329,264 baseline spend, and it happens to equal the best-case 2% move, so it was a reused scenario figure, not a quote. It is no longer a lock cost anywhere: the re-rendered brief and evidence trail show "Cost of action: Not quantified - requires a supplier quote for the price-lock premium and volume commitment." and "Risk protected: protects against the full worst-case exposure of £39,512." The saved rationale text itself is original run data and is unchanged; the pages add a note next to it.

## Files

- `brief_20261024_215151.md`: the CFO brief, **re-rendered, not the original file.** Rebuilt from `world_state_d9decb7d.json` with the current brief code (no model calls): it shows the real Red Team objection count for D1, the default-band label and the price-lock wording. The run's data is unchanged.
- `run_20261024_212648.jsonl`: the trace of every step. `world_state_d9decb7d.json`: the run's shared memory.
- `evidence_trail.html`: **re-rendered, not the original file.** It was rebuilt from `world_state_d9decb7d.json` with the current page code (no model calls), so D1 shows the real objection count and "challenge unresolved" instead of the old "not challenged, 0 objections" label, and the lock and band wording are the current ones.
