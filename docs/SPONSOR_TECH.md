# Sponsor technology: how it is actually used

Evidence below comes from one complete live run, `python main.py --demo` on 2026-10-24
(`output_examples/demo_run_20261024_v2/`: brief, evidence trail page, and `run_20261024_144545.jsonl`).
Company data is synthetic for privacy; FX and news are live.

## Zetaris - the data layer

- Ganga's purchase data is in Zetaris, not read from CSV at run time. `scripts/load_ganga_to_zetaris.py` copies `data/*.csv` into the Zetaris
  volume and registers three tables in a `ganga` database: `ganga.parts`, `ganga.suppliers`, `ganga.purchases`.
- Agents read it with SQL through `core/zetaris_client.py` (REST). Used for: the latest quarter, spend per part and supplier country
  (`core/ganga_data.py`), the part-to-country catalogue the Evaluator checks claims against, and the per-part rows the calculator
  (`core/calculator.py`) turns into GBP figures.
- Evidence: the demo run's trace has 6 `zetaris` SQL events (health check `SELECT 1`, and the queries above). Every query is logged.

## Meterless Nightshift - the AI runtime

- Every model call goes through `core/llm.py` to the Nightshift OpenAI-compatible endpoint (`METERLESS_BASE_URL`, token in `.env`, never committed).
  There is no other AI provider in the code. Retries, per-call timeouts, the demo time budget and the empty-response retry live in that file.
- All agents request the lane `vidhur-analyst` (`core/config.py`, `.env.example`).
- Evidence: in the demo run all 14 successful calls have `requested_lane: vidhur-analyst`, `pin_honoured: true`.
  Per agent: Decision Maker 4, Red Team 5, Evaluator 2, Geopolitics Analyst 2, Currency Analyst 1. 1 further attempt failed with runtime
  502 (`empty_response`) and was retried; each failure is in the trace with its routed model.
- Honest gap: this Nightshift version does not expose receipt ids in the responses, so `receipt_id` is empty in all 14 call lines.
  The evidence that a call happened is the trace line (agent, lane, answering model, token counts, latency), not a Nightshift receipt.

## Meterless World Model engine - shared memory

- Agents never pass data to each other directly. They write to and read from one World State (`core/shared_state.py`, class `RunWorld`),
  built on the engine in `engines/world_model/`: event-sourced append-only log, content-derived stable ids, provenance on every write,
  bitemporal edges, idempotent ingest, audit log. It is a Python implementation of the official spec, not the TypeScript reference itself.
- `tests/test_world_model_engine.py` ports the spec's 8 conformance checks 1:1, plus golden-data tests against the reference
  (`tests/data/world_model_golden.json`). Details and the two documented differences: `docs/meterless_engine.md`.
- Evidence: the demo run wrote 1,290 world-state events (claims, verdicts, impacts, decisions, objections, outcomes). The brief and
  `evidence_trail.html` are both built from that world-state report. The raw `logs/world_state_<run_id>.json` is not committed.

## NVIDIA Nemotron - the model

- Every AI call is answered by `nvidia/nemotron-3.5-lightning-30b-a3b` (Nemotron 3.5 Lightning). Evidence: `answered_model` is this id on all 14
  successful calls in the demo trace, about 43k prompt and 61k completion tokens (the completion count includes the model's hidden reasoning).
- Lightning is the only model used. `nvidia/nemotron-3-super-120b-a12b` answers 502 `nvidia_404 ... Function not found for account` on this
  account and `nvidia/llama-3.3-nemotron-super-49b-v1.5` answers 410 Gone (checked 2026-10-24, `scripts/check_lanes.py`), so there is no
  larger model and no cross-model check.
- The model reasons even when asked not to. For the Decision Maker and Red Team, short budgets came back empty, so those two agents run with
  reasoning on and an 8000-token budget (`think=True` in `core/llm.py`).

## Claude Code - the coding tool

Vidhur was written with Claude Code (Anthropic's coding agent) working with the builder. It is a development tool only: Claude is not called
by the product at run time. The organisers made Cursor optional.
