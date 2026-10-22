# Vidhur

**The Agent That Can Explain Why** - an entry for the Open Agent Hackathon 2026 (GenAI Works), Track 2.

Vidhur is a multi-agent cost-risk agent for a **fictional** company, **Ganga AI Solutions** (a UK maker of GPU / AI servers).
It reads Ganga's purchase history, checks the news for risks to what Ganga buys, and tells the CFO how much a risk could
cost in GBP - as a range (worst / likely / best) with a confidence level and the evidence behind it.

The point of the track: **every recommendation carries evidence.** An independent Evaluator (the auditor) checks each claim
before it can reach the CFO brief.

## What works today (Day 1)

```
Zetaris health check
  -> pick Ganga's top-spend parts / supplier countries (SQL on Zetaris)
  -> Geopolitics Analyst: real news -> structured claims (Nemotron via Meterless Nightshift)
  -> Evaluator: deterministic checks, then a Nemotron "do the sources support the claim?" check
       PASS  -> continue
       SEND_BACK -> back to the analyst with the reason (max 2 retries), then UNVERIFIED
  -> GBP impact (plain Python from Zetaris data, not the model): worst / likely / best + confidence
  -> CFO brief (output_examples/brief_<timestamp>.md) + trace log (logs/run_<timestamp>.jsonl)
```

The Evaluator's deterministic checks: at least 2 sources; from different publishers (same publisher or the same syndicated
headline counts as one source); fresh (within 30 days, configurable); every cited URL really is in the fetched news; and the
part and supplier country really exist in Ganga's Zetaris data. Only then does Nemotron judge whether the sources support the claim.

## Ganga's data is synthetic

Everything in `data/` (parts, suppliers, quarterly prices and quantities) is **made up** and pre-prepared for the hackathon.
Ganga AI Solutions does not exist. See `data/README_DATA.md`. The **news evidence is real**: it is fetched live from free RSS
feeds (Google News search plus BBC, The Guardian, Al Jazeera, NPR, DW).

## How to run it

Prerequisites: Docker Desktop with the Zetaris platform running (UI http://localhost:3000, API http://localhost:8888),
and the Meterless Nightshift app running (http://127.0.0.1:8088/v1).

```powershell
python -m venv venv                  # first time only
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env               # then fill in the token / Zetaris login (never commit .env)

python scripts/load_ganga_to_zetaris.py   # copies data/*.csv into Zetaris and registers tables; safe to run twice
python main.py                            # the full Day 1 loop
python main.py --demo-weak-claim          # see the Evaluator catch a weak claim (below)
python -m pytest tests                    # unit tests; integration tests skip if Zetaris/Nightshift are down
```

Useful options: `--targets N` (how many top-spend part/country pairs, default 3), `--refresh-news` (ignore the news cache).

### `--demo-weak-claim` (a demo flag, not hidden faking)

With this flag the analyst's **first attempt** gets one extra instruction: cite exactly one source. The model call is real and
the Evaluator's check is real; only the analyst's instruction changes. The Evaluator then sends the claim back
(`TOO_FEW_SOURCES`), the analyst revises it with the feedback, and the brief shows the full audit history. The flag is printed in
the console, recorded in the trace (`demo_weak_claim: true`) and stated at the top of the brief.

## Where things are

| Path | What |
|---|---|
| `core/zetaris_client.py` | Zetaris REST client - one Lightning SQL statement per request |
| `core/llm.py` | Meterless Nightshift client (OpenAI-compatible), pinned Nemotron models, retries |
| `core/trace.py` | Append-only JSONL trace of every step (agent, model, tokens, latency, sources, decision) |
| `core/news.py` | RSS evidence fetching; raw articles cached in `data/news_cache/` (never final answers) |
| `core/impact.py` | GBP impact maths and confidence levels |
| `agents/geopolitics_analyst.py` | Geopolitics Analyst |
| `agents/evaluator.py` | The Evaluator and the send-back loop |
| `scripts/load_ganga_to_zetaris.py` | Registers Ganga's CSVs as Zetaris tables |
| `tests/` | Unit tests plus integration tests |

## Notes on the AI runtime

All product AI calls go through Meterless Nightshift. The analyst uses the Nightshift lane `vidhur-analyst`
(nvidia/nemotron-3.5-lightning-30b-a3b); the Evaluator is configured for the lane `vidhur-evaluator`
(nvidia/nemotron-3-super-120b-a12b). `.env` holds the lane id to request (`MODEL_*`) and the Nemotron model that must answer
(`MODEL_*_EXPECTED`). The trace records the requested model, the expected model, the model that actually answered and
`pin_honoured` for every call. The analyst lane is honoured reliably; the evaluator lane is currently answered by the
lightning model instead (see Limitations). The runtime enforces a ~45 s per-request deadline and answers 502 when a long
reasoning call overruns it, so the client retries automatically (default 5 attempts, `LLM_MAX_ATTEMPTS`) and logs every failed attempt.

## Limitations / not built yet

- **Only the Geopolitics Analyst exists.** Commodity, currency, logistics, trade & tariff and supplier-health analysts are not built.
- **No Planner (Vidhur) yet.** Today `main.py` simply takes the top-spend parts; nothing decides which risks matter or delegates.
- **No Meterless World Model engine, no Red Team, no Docker packaging.**
- **Evidence is mostly headlines.** Google News RSS gives titles and publishers, not article text, so the Evaluator judges
  headlines (plus summaries where a publisher feed gives one). It does not read full articles.
- **No "trusted publisher" list yet.** Independence and freshness are checked; whether a publisher is reputable is not.
- **Percentage ranges are the model's own estimates**, not figures from the sources. The GBP maths is exact, but the inputs are estimates.
  The brief says so.
- **Baseline is one quarter** (the latest quarter in the data); no seasonality, hedging or contract terms.
- **The Evaluator is not yet served by its own lane**: requests for `vidhur-evaluator` / the super-120b model are answered by the lightning model, so analyst and auditor currently share a model. Runs also hit provider 502s (see above). A target whose model calls keep failing is
  reported as "not assessed" rather than guessed.
- Synthetic data only; nothing here is financial advice.
