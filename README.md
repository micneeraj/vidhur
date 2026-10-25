# Vidhur

**LLMs hallucinate numbers. Vidhur forces every agent claim through a deterministic audit checklist and an adversarial Red Team before a CFO sees a single pound sign.**

Vidhur turns scattered market events and company spend into a CFO-ready (for cleared decisions; pending-review decisions are labelled as such), evidence-backed cost decision, with every number traceable. Every cleared recommendation has been challenged; incomplete challenges are marked pending review.

**Built by a commercial auditor. Vidhur applies professional scepticism: no number without evidence, no cleared action without challenge.**

*Open Agent Hackathon 2026 (GenAI Works), Track 2 - "The Agent That Can Explain Why".*

**Professional scepticism.** The **Evaluator** and the **Red Team** are Vidhur's *professional scepticism* layer: the auditor's way of never accepting a
claim, or a recommendation, without evidence. The Evaluator will not let a claim reach the CFO unless independent, fresh, trusted sources support it and it matches
Ganga's own data; the Red Team then attacks the resulting decision before the CFO sees it. The Evaluator checks every number in a claim against the cited
excerpt, the real FX rate and the calculator; the Red Team rejects any revision that does not give a written reason tied to evidence or a calculation.
How well that works is measured, not asserted: see **Initial adversarial evaluation** below.

## Before and after

| | |
|---|---|
| **Before** | An analyst manually checks the news, FX rates and supplier exposure, builds a spreadsheet, and spends hours defending the assumptions in it. |
| **After** | Vidhur produces a ranked cost-impact brief with citations, calculation records, Red Team objections and a recommended action in minutes. |

The honest number for "minutes": a live `--demo` run (two drivers, before the third analyst was added) took **341 s to 990 s (about 6 to 16 minutes)** across the five recent runs, and the two kept complete runs took 738 s and 905 s. A full run is 15-20 minutes on a healthy runtime and longer when the runtime is busy. That time buys verification (parallel analysts, SQL cross-checks, deterministic checks, a Red Team loop), not just text generation.

## Three proof moments

1. **A weak claim is sent back, then verified.** The Taiwan GPU claim cites one source; the Evaluator's deterministic check sends it back (`TOO_FEW_SOURCES`), the analyst revises with the feedback, and it passes with a second source. The whole audit history is in the brief. See `output_examples/demo_run_20261024_v3/`.
2. **The Red Team changes the decision, with written reasons.** The first proposal is a 100% hedge of the part's spend. The Red Team raises objections tied to evidence ids and numbers; the Decision Maker revises it to a 20% second-supplier shift, and a revision without a written reason is rejected. Same folder, and `demo_run_20261024_v2/`.
3. **Honest COULD NOT VERIFY.** In a live run the Taiwan claim was sent back for `TOO_FEW_SOURCES`, the analyst found no better evidence, and Vidhur labelled it **COULD NOT VERIFY** (no GBP figure, and explicitly not zero impact) instead of inventing a number. The other driver (EUR) was verified, challenged twice by the Red Team, and ended at no action. See [`output_examples/live_run_20261024_could_not_verify/`](output_examples/live_run_20261024_could_not_verify/README.md).

## CFO dashboard

`output_examples/dashboard.html` is one self-contained page (inline CSS and a little JavaScript, no CDN, no server) that answers the CFO's question for a saved run: the verified GBP change, what to do per driver, proof chips, and a click-to-expand "why chain" per driver (claim, sources, Evaluator checks, send-back, GBP calculation, first proposal, Red Team objections and written responses, final decision, what would change it). **To open it, double-click the file**; the buttons at the top switch between the five saved runs (the v3 run, which opens by default, the 3-driver run, the D3 focused run, the live "could not verify" run and the 25 Oct run with D1 contested).

It is built only from saved run files (`world_state_<id>.json` and the JSONL trace in each run folder): every number on the page is read from those files, an element whose value is not in them is left out, and a driver that could not be verified shows no GBP figure. No model is called. Rebuild it with `python main.py --dashboard` (or `python scripts/build_dashboard.py`); every normal `python main.py` run also rebuilds it and adds that run as "Latest run" (only the most recent one is added). Code: `core/dashboard.py`; tests: `tests/test_dashboard.py`.

## How it works

Vidhur is an **auditor's agent for cost risk**. For a fictional UK AI-server maker, **Ganga AI Solutions**, it looks at what the company buys, asks
specialist analysts what could push those costs up (export controls, tariffs, currency moves, memory-chip prices and supplier concentration), and then **checks every claim before it counts**:
against trusted, independent, fresh sources (ranked by publisher) and against the company's own purchase data (read from Zetaris with SQL).
Claims that cannot be verified get no number. The claims that survive become a **decision** (hedge, lock the price, add a second supplier, or do
nothing) and a **Red Team** challenges that decision, like a sceptical CFO, **before it reaches the CFO brief**. Every GBP figure is computed in
plain Python, every step is traced, and the brief says which evidence supports each number.

Example of the Planner skipping a part, with its stated reason (saved run `0558c4cb`, `logs/world_state_0558c4cb.json`): *Skipped P008 GangaFrame 42U Chassis Rack (geopolitics): Lowest spend (GBP 75,795, 3%); no headline or FX data linking to cost risk; analyst effort better allocated to higher-impact parts.* Every skip is recorded with a reason; demo runs use a fixed plan instead and record "Outside the three-driver demo scope".

### Two feedback loops

- **Evaluator send-back loop.** The Evaluator audits each *claim*. A failed check returns the claim to the analyst with the reason (up to 2 send-backs); a claim that still fails is shown as Could not verify, with no GBP figure.
- **Decision Maker <-> Red Team challenge loop.** The Red Team audits each *decision*. It raises objections that must cite evidence; the Decision Maker revises (with a written reason) or defends; the Red Team re-judges (up to 2 rounds). A high objection still standing marks the decision CONTESTED for the CFO.

### Architecture

The loop, in one picture:

```text
                       Zetaris  (Ganga's purchase data, SQL)
                           |
                       Planner  --- decides who works on what, and why
                           |
        +------------------+------------------+
        v                  v                  v
 Geopolitics Analyst  Currency Analyst  Supplier & Commodity Analyst   (run in parallel)
        |   claim          |   claim          |   claim
        +------------------+------------------+
                           v
                      Evaluator  --- audits the CLAIM: 2+ independent fresh sources, trusted tier,
                       |    |        real URLs, matches Ganga data
          SEND_BACK +  |    | PASS + GBP impact (Python)
          reason (max 2)    v
       back to analyst   Decision Maker  <------------->  Red Team   --- audits the DECISION
                           |             objections /      (max 2 rounds; a high objection left
                           |             revise or defend   standing = CONTESTED)
                           v
                      CFO brief

   World State (Meterless World Model) = the shared memory every agent writes to and reads from; the brief is built from it.
   Nightshift + NVIDIA Nemotron = the only AI. Trace log = every step, model, latency.
```

The same loop with more detail:

```mermaid
flowchart LR
    Z[(Zetaris<br/>Ganga's purchase data<br/>via SQL)] --> P
    N[Meterless Nightshift<br/>Nemotron, lane vidhur-analyst] -.model calls.-> P & G & C & E & D & R
    P["Planner - Vidhur<br/>who works on what, and why"] -->|delegates| G["Geopolitics Analyst<br/>news: export controls, tariffs"]
    P -->|delegates| C["Currency Analyst<br/>ECB FX data, computed in Python"]
    P -->|delegates| S["Supplier & Commodity Analyst<br/>news: HBM/DRAM prices, supplier capacity"]
    S -->|claim| E
    G -->|claim| E["Evaluator - audits the CLAIM<br/>2+ independent fresh sources,<br/>Tier 1/2 source, real URLs, matches Ganga data"]
    C -->|claim| E
    E -->|"SEND_BACK + reason (max 2)"| G & C & S
    E -->|"PASS + GBP impact (Python)"| D["Decision Maker<br/>picks an option; GBP computed in Python"]
    E -->|"UNVERIFIED after 2 retries"| W
    D -->|proposed decision| R["Red Team - audits the DECISION<br/>sceptical CFO: objections with evidence ids"]
    R -->|"objections (high / medium / low)"| D
    D -->|"REVISE or DEFEND (max 2 rounds)"| R
    R -->|"high objection still open"| X["CONTESTED<br/>needs CFO judgement"]
    W[("Meterless World Model<br/>shared event-sourced memory<br/>logs/world_state_RUN.json")]
    P <-->|plan, review, re-delegate once| W
    G <--> W
    C <--> W
    E <--> W
    D <--> W
    R <--> W
    D --> B
    X --> B
    W --> B[CFO brief<br/>built from the world state]
    T[Trace log<br/>logs/run_TIMESTAMP.jsonl] -.every step.-> W
```

What happens in a run (`python main.py`):

1. **Zetaris health check**, then Ganga's spend by part and supplier country (SQL on Zetaris, not the CSV files).
2. **Planner (Vidhur)** reads that spend plus a quick headline scan and decides *which analysts run for which parts and why* (a real
   model call; the plan is validated in Python and may skip analyst/part pairs, e.g. no FX data exists for the Taiwan dollar).
3. **Analysts run in parallel** (3 workers). The Geopolitics Analyst turns the 8 most relevant news articles into a claim; the
   Currency Analyst computes the real 30/90-day move of the supplier's currency against the pound from ECB data in Python and the model only explains it;
   the Supplier & Commodity Analyst runs the same news pipeline (trust-ranked evidence, claims in the same format) on memory-chip price moves, supplier capacity,
   concentration and disruption (e.g. HBM/DRAM contract prices, export rules that stop the supplier shipping). It is built for the South Korean HBM part and any
   other commodity-driven part the Planner picks. Its % range is `FORECAST` only if a cited article prints the figure, otherwise a labelled `SCENARIO` assumption;
   it has no price series of its own, so it never reports `OBSERVED`.
4. **Evaluator** reads each claim from the world state: deterministic checks first (2+ sources, different publishers, fresh, every URL really
   fetched, part and country exist in Zetaris, FX numbers match the computed ones), then a Nemotron "do the sources support the claim?" check.
   Then the trust rules: at least one fresh Tier 1 or Tier 2 source, copies of one report count once, stale sources do not count.
   `SEND_BACK` returns the claim to the analyst with the reason (max 2 retries), then `UNVERIFIED`.
5. **Planner reviews** the world state and may **re-delegate once** (e.g. ask the other analyst about a high-spend part that ended unverified).
6. **GBP impact** is plain Python from Zetaris data (latest-quarter spend x % range); confidence comes from the Evaluator result.
7. **Decision Maker** proposes one decision per verified claim (no action / hedge a share / lock the price / second supplier) and the **Red Team**
   challenges it (next section). The brief's *Recommended decisions* section compares the options in GBP.
8. **CFO brief** (`output_examples/brief_<timestamp>.md`) is built from the world state: plan and reasons, verified claims with worst/likely/best GBP,
   UNVERIFIED items, evidence trail, and pointers to the trace and the world-state file.

### Sponsor technology

- **Zetaris - the data layer.** Ganga's data lives in Zetaris, not in the CSV files. `scripts/load_ganga_to_zetaris.py` copies `data/*.csv` into the
  Zetaris volume and registers three Lightning filestore tables in a `ganga` database: `ganga.parts`, `ganga.suppliers`, `ganga.purchases`.
  The agents read them with SQL through `core/zetaris_client.py` (REST, one statement per request): the latest quarter (`SELECT MAX(quarter) FROM ganga.purchases`),
  spend per part and supplier country (`ganga.purchases` joined to `ganga.parts` and `ganga.suppliers`), a part-to-country catalogue the Evaluator
  checks claims against, and per-part quarterly spend for the GBP impact. Every query is logged in the trace (`zetaris:sql`).
- **Meterless - the World Model engine.** The shared memory is the Meterless World Model, implemented in Python from the official spec in `engines/world_model/`
  (event-sourced append-only log, content-derived stable ids, provenance on every write, bitemporal edges, idempotent ingest, audit log). Agents never
  pass data to each other directly: they write to and read from this World State, saved per run as `logs/world_state_<run_id>.json`.
  **Conformance test path:** `tests/test_world_model_engine.py` is the spec's 8 conformance checks (`sid-001 sid-002 band-001 prov-001 idem-001 idem-002
  bitemp-001 audit-001`) ported 1:1 to Python, plus golden-data tests against the TypeScript reference (`tests/data/world_model_golden.json`).
  Run `python -m pytest tests/test_world_model_engine.py`. Details and the two documented differences from the reference: [`docs/meterless_engine.md`](docs/meterless_engine.md).
- **Meterless Nightshift - the only AI provider.** Every model call goes through `core/llm.py` to the Nightshift OpenAI-compatible endpoint. There is no other AI provider in the code.
- **NVIDIA Nemotron - the model.** Nightshift lanes resolve to Nemotron. Every AI call line in the trace records `requested_lane`, `answered_model` and `pin_honoured`,
  so the evidence of which Nemotron model actually answered is in the trace files (`logs/run_*.jsonl`). `python scripts/check_lanes.py` re-tests the lanes.
- **Built with Claude Code.** The organisers made Cursor optional, so this was built with Claude Code.

### Numbers are calculated in Python

The AI never directly generates a GBP amount. It supplies evidence, direction and a labelled percentage range; Python alone converts verified inputs and company spend into GBP using recorded formulas and FX rates. The analysts output only a **driver**, a **direction** (cost up / down), **evidence** (url, publisher, date and an excerpt taken from
the fetched article, not from the model) and, **only if a cited source prints one**, a percentage. `core/calculator.py` then computes the GBP impact deterministically from
Ganga's Zetaris purchase rows (part, supplier country, quantity, unit price, currency), a real ECB FX rate **with its date**, and the explicit assumptions in
`data/decision_assumptions.csv`:

```
baseline_gbp = sum(quantity x unit_price / units_of_currency_per_GBP)
change_gbp   = baseline_gbp x move% x pass_through%      (+ cost up, - saving)
```

Every figure in the CFO brief carries a **calculation record** (input rows, FX rate and date, formula, assumptions) and one label:

| Label | Meaning | Example |
|---|---|---|
| **OBSERVED** | read from data | Zetaris quantity and price, the ECB rate, the baseline spend |
| **FORECAST** | stated by a cited source | a percentage an article prints (the Evaluator checks it is in the excerpt) |
| **SCENARIO** | our assumption | the 2% / 6% / 12% default band, the FX pass-through, the hedge premium |

When no source states a percentage the move comes from the SCENARIO rows of `data/decision_assumptions.csv`. Worst / likely / best is called a **scenario range, not a
probability**. The brief opens with one line, from the calculator, on why this matters (what a wrong 50% hedge would cost in GBP) and has a **"How this number was
calculated"** section for every driver. If the live FX call fails the rate stored on the Zetaris row is used and the record says so.

**The default band, and the same band for memory.** Wherever the band is shown for any driver (brief, evidence trail, dashboard, this README) it carries this label: "2-12% is Ganga's default SCENARIO band for a verified directional cost driver with no directly comparable, quantified forecast for this part and quarter. It is not a probability and is not part-specific." D3 (memory) uses the same band as the other drivers; there are no memory-specific scenario rows and no memory-specific calculator logic. A memory-specific band will be added once it can be anchored to a cited, part-relevant forecast.

The Evaluator's deterministic checks (no AI, run before the model check) enforce this; a failed check uses the normal send-back loop (max 2 retries, then UNVERIFIED):

| Code | Fails when |
|---|---|
| `NUMBER_NOT_IN_SOURCE` | a % or figure in the claim is not in a cited excerpt and is not tagged `(SCENARIO)` (a range labelled FORECAST must be printed by a source) |
| `FX_MISMATCH` | a claimed FX rate differs from the real ECB rate for its date by more than 2%, the claimed move does not follow from its own rates, or the rate cannot be checked |
| `DIRECTION_MISMATCH` | the claim's direction differs from the one Python derives from the ECB data: 1 GBP buys fewer units of the currency means the part costs more in pounds (`cost_up`), more units means `cost_down` |
| `CURRENCY_COUNTRY_MISMATCH` | the currency is neither the supplier country's (Taiwan: TWD, USD as proxy; Korea: KRW; Germany: EUR; China: CNY; also Japan JPY, Malaysia MYR) nor the currency Zetaris prices the part in |
| `FX_PAIR_MISMATCH` | the FX pair of a currency claim differs from the currency of the Zetaris purchase row the GBP calculation used (for example a GBP/EUR claim on a part priced in USD) |
| `GBP_MISMATCH` | a GBP figure in the claim is more than 10% away from every calculator result for that part (or there is no calculator result to check it) |
| `DUPLICATE_SOURCE` | sources with the same outlet, the same original outlet ("via X"), the same headline or a near-identical excerpt count as one and fewer than two independent sources remain |
| `STALE_SOURCE` | a source is older than `EVIDENCE_MAX_AGE_DAYS` (config, default 30) |

### Decision Maker and Red Team (the challenge loop)

The Evaluator checks the **claims**; the **Red Team** checks the **decision** built on them, like a sceptical CFO or risk committee.

- **Decision Maker** (`agents/decision_maker.py`) reads the verified claim from the World State and Ganga's spend from Zetaris and picks one option
  with a rationale. **Every GBP figure is computed in Python** (`core/decisions.py`): cost under worst / likely / best and "if the forecast is
  wrong" (the mirror of the likely case), for no action, a 50% hedge and a 30% second-supplier shift. The hedge (1.5%) and second-supplier (3%) premiums
  are SCENARIO assumptions in `data/decision_assumptions.csv`, not quotes.
- **A price lock has no invented cost.** No price-lock premium is documented, so the lock is never given a GBP cost and is never shown with a scenario outcome as its cost. The brief,
  evidence trail, dashboard and the inputs to the Decision Maker and Red Team show instead: "Cost of action: Not quantified - requires a supplier quote for the price-lock premium and
  volume commitment."; "Risk protected" (the worst case times the locked share, computed in Python); "Trade-off: locking may forgo savings if prices soften."; and what would change the
  recommendation (a supplier quote showing the lock premium exceeds the expected avoided cost, or evidence that supply/pricing is easing).
- **No hedge or price lock when the verified expected direction is cost DOWN (deterministic rule, before the AI proposal):** if the worst case of the scenario range is not a cost increase (every scenario is a saving),
  `decisions.rule_choice` fixes the decision to NO_ACTION / monitor, with no model call and no hedge, price lock or second supplier. It is not a blanket ban: protection is allowed against a cost rise. This holds in the aggressive demo too, and a
  Red Team revision cannot bring a hedge back. Unit-tested in `tests/test_calculator.py`.
- **Cleared and pending decisions.** A decision whose Red Team loop completed is **DECISION CLEARED**. A decision whose Red Team did not complete (timed out, unresolved, unreviewed, budget hit) is **DECISION PENDING REVIEW** and its action is shown as "Provisional recommendation: <action>. Why provisional: Red Team review did not complete within the runtime budget; not cleared for CFO action." The state is derived in code from the saved outcome (`decisions.review_state`), not written by hand; cleared is teal and pending is amber on the dashboard. A decision whose loop completed with a high objection still standing is **DECISION CONTESTED** (needs CFO judgement), never cleared.
- **Red Team** (`core/red_team.py`) returns structured JSON objections of five kinds: forecast wrong (opposite scenario), cost of acting vs not
  acting, concentration (supplier / country), weakest evidence, cheaper alternative. Each has a severity and must cite evidence ids (S1.., the
  work id, an option name) or numbers present in the evidence. Objections without a reference are dropped; if none survive, the model is re-asked once.
- **Challenge loop:** Red Team objects -> Decision Maker **REVISES** (new option or share, GBP recalculated in Python; **a revision without a written reason tied to evidence
  (an S-id) or a calculation (a number from the CALC line) is rejected**) or **DEFENDS** (needs evidence references) every high and medium objection -> Red Team re-judges -> one more round at most. **Maximum 2 rounds.** A high objection still standing
  afterwards marks the decision **CONTESTED - needs CFO judgement** instead of a confident recommendation.
- Objections, responses, outcomes and every decision version are written to the World State and logged to the trace
  (`objection`, `response`, `revised`, `review`, `round`, `outcome`). The brief shows, per decision, "Challenged by Red Team" with the top objections,
  how each was handled and the before / after GBP.

### Trusted-source ranking


Not every source is equal, so Vidhur ranks evidence by who published it, not only by how relevant it is.

- **The list** is `data/source_trust.csv` (`domain,publisher,tier,score`), a curated, editable config (see `data/SOURCE_TRUST_README.md`).
  Tier 1 = 1.0 (wires, top business press, central banks, statistics offices, WTO / IMF / World Bank, semiconductor trade bodies),
  Tier 2 = 0.7 (known business and trade press), Tier 3 = 0.3 (any unlisted domain, the default).
- **Mapping** (`core/source_trust.py`): URL -> (publisher, tier, score). Subdomains count as their parent (`uk.reuters.com` is `reuters.com`).
  Google News links point at `news.google.com`, so for those the publisher name decides.
- **Ranking before the AI sees anything:** `combined_score = 0.6 * relevance + 0.4 * trust_score`, relevance being the existing keyword and recency
  score normalised to 0-1. Only the top N (unchanged) go to the model, which cuts tokens. Kept and dropped articles are logged to the trace
  with their scores (`evidence:rank`).
- **Evaluator rules** (added to the existing checks, new send-back codes): `NO_TRUSTED_SOURCE` (no fresh Tier 1 or 2 source),
  `SOURCES_NOT_INDEPENDENT` (same domain root or near-identical headlines count once), `STALE_SOURCES` (sources older than `EVIDENCE_MAX_AGE_DAYS`,
  default 30, do not count), next to `TOO_FEW_SOURCES`. Max 2 retries, then UNVERIFIED, as before.
- **CFO brief:** every source shows publisher, tier and date, and each claim has an evidence-strength line such as
  "2 independent sources, 1 Tier 1".

## Confidence explained

**Confidence reflects evidence quality and source agreement, not the size of the impact. Low = investigate before committing money; High = fine for routine action or monitoring.**

Confidence is computed in Python (`core/impact.py`) from the Evaluator result: 3 or more independent sources start at High, 2 at Medium; one level is dropped if the claim only passed after being sent back, and one more if the model check judged the support weak.

Every decision in the CFO brief and in the evidence trail page also has a **What would change this decision** section, generated in Python from data the run already holds (`core/next_steps.py`, no model):

- **What would raise confidence:** what is missing from that claim's own sources (for example "a second independent Tier 1 source", or a source that states the size of the move).
- **Re-run trigger:** for a currency driver, "re-run if GBP/EUR moves more than 2% from 1.1943 EUR per GBP on 2026-10-23", the 2% being `RERUN_FX_MOVE_PCT` in `data/decision_assumptions.csv`; for a geopolitics driver, "re-run on new Tier 1/2 news about <the driver>". These are triggers for a person to act on; Vidhur does not watch rates or news between runs.
- **For a Could not verify driver:** "Next step: find two independent sources (one Tier 1 or 2) before any decision; no GBP figure until then."

## Data boundary

| | What | Where |
|---|---|---|
| **Synthetic** | Ganga AI Solutions' purchases, suppliers and parts (a fictional UK AI-server maker) | `data/parts.csv`, `data/suppliers.csv`, `data/purchases.csv`, loaded into Zetaris |
| **News and FX** | News (free RSS feeds) and ECB exchange rates (Frankfurter) | News is cached and dated for reproducibility (`--demo` reads `data/demo_evidence/`; a normal run fetches it at run time and dates every article); ECB FX rates are fetched live |
| **Our own config** | Source trust tiers; decision assumptions (premiums, scenario moves, pass-through, re-run threshold) | `data/source_trust.csv`, `data/decision_assumptions.csv` |
| **Generalisable** | Any company can load its own purchase data into Zetaris using the same table schema and run the same pipeline | `scripts/load_ganga_to_zetaris.py` |

The three Zetaris tables (`ganga.parts`, `ganga.suppliers`, `ganga.purchases`) have exactly the columns of the CSV headers:

| Table | Columns |
|---|---|
| `parts` | `part_id`, `part_name`, `category`, `supplier_id` |
| `suppliers` | `supplier_id`, `supplier_name`, `country`, `part_category` |
| `purchases` | `quarter`, `part_id`, `quantity`, `currency`, `unit_price`, `unit_price_gbp`, `gbp_per_currency_unit`, `total_gbp` |

**Ganga AI Solutions is fictional and its data is synthetic**, prepared before the build window. Everything in `data/` (parts, suppliers, quarterly prices
and quantities) is made up; see `data/README_DATA.md`. The **evidence is real**: news from free RSS feeds (Google News search plus BBC, The Guardian,
Al Jazeera, NPR, DW) and exchange rates from the free ECB-based Frankfurter API.

`data/decision_assumptions.csv` (`option,premium_pct,note`) holds the premiums the Decision Maker uses for a hedge (1.5%), a price lock (2%) and a second
supplier (3%), and the SCENARIO rows the calculator uses (`SCENARIO_SMALL/LIKELY/LARGE_MOVE_PCT` = 2 / 6 / 12, `FX_PASS_THROUGH_PCT` = 100), and `RERUN_FX_MOVE_PCT` = 2 (the FX move, in percent, after which a person should re-run Vidhur). They are **illustrative
assumptions, not market quotes**, and the CFO brief points to the file under every option comparison and calculation.

## Why these drivers

For Ganga, three exposures cover most of the spend. In the latest quarter in the data (2026-Q3, GBP at the rates stored on the purchase rows, total about £2.55m):

| Part | Supplier country | Currency | Quarterly spend | Share |
|---|---|---|---|---|
| P001 GangaAccel GA100 GPU Chip | Taiwan | USD | £1,344,975 | **52.7%** |
| P006 GangaCool Direct-to-Chip Loop | Germany | EUR | £469,238 | **18.4%** |
| P002 GangaHBM-8H Memory Stack | South Korea | USD | £330,120 | 12.9% |
| the other five parts | China, Malaysia, Japan | USD | £409,633 | 16.0% |

So Taiwan chip supply (geopolitics), the EUR-priced German cooling parts (currency) and the South Korean HBM memory stack (supplier and commodity) are the three largest identified exposures, about 84% of Q3 spend (52.7% + 18.4% + 12.9%, from the Zetaris data), and `--demo` runs exactly those three drivers, D1, D2 and D3. The Korean part is priced in USD in the data, not KRW, so its currency exposure is GBP/USD; the new analyst covers the risk the other two do not: memory contract prices and the concentration of supply in one supplier (Hanseong Memory Works, S002). In a normal run the Planner chooses which analyst works on which part, with a written reason, from the Zetaris spend.

D3 is selected by materiality (Korean HBM memory, 13% of spend) because memory is a distinct cost mechanism - industry supply and wafer allocation - rather than a repeat of geopolitical or FX analysis. The three drivers are the three largest identified exposures, about 84% of Q3 spend.

D3 status: D3 completed claim verification and the GBP scenario calculation; its Red Team step hit the runtime budget in the saved live run. The Red Team logic is implemented and tested; a completed D3 Red Team run is next work.

In the CFO brief and the dashboard D3 card: "Memory exposure is driven by industry capacity allocation and HBM supply tightness, separate from the Taiwan export-control and FX drivers."

Three analysts (geopolitics, currency, supplier & commodity) exist today. **Adding another analyst is a small registration, not a rewrite, but it is not zero code:** write the analyst class, then register it in `ANALYSTS` in `agents/planner.py` and in the `analysts` dict in `main.py`. The Planner prompt, delegation validation and the work-item loop (`agents/workflow.py`) already accept any registered name.

- **No freight analyst in this version.** Ganga's high value parts (GPUs, memory chips, AI servers) mostly ship by air and freight is a small share of landed cost,
  so it would add little to the CFO decision. A freight analyst is future work (a memory-price analyst now exists, see above). Adding one is a small change, not a rewrite: write the analyst class and
  register it in `ANALYSTS` (`agents/planner.py`) and in the `analysts` dict in `main.py`; the Planner's prompt, its validation of delegations and the work-item
  loop (`agents/workflow.py`) already accept any registered analyst name. It is not zero-code: the registration is needed.
- **Numbers are computed in Python, never by the model.** FX moves, GBP impacts and decision costs come from Zetaris data and plain arithmetic; the model explains and picks.
- **Unverified means no number.** A claim that fails the Evaluator after two send-backs is shown as unverified with no GBP figure.

## Initial adversarial evaluation

**What was tested.** A set of 25 planted claims written by the builder: 20 deliberately false or weak claims and 5 correct controls, run through the Evaluator. The planted failure types in the set (`data/eval/planted_claims.csv`) are: wrong FX number (3: range 3x the real move, opposite direction, invented moves), wrong currency pair (1), stale source (2), untrusted publisher only (2), single source (2), copied report (2), wrong supplier country (2), GBP impact that does not match Ganga's data (2), cause not supported by the cited sources (2) and wrong date (2). The 5 controls are well-supported claims. A further 6 claims (`planted_claims_new.csv`) were added, one per new deterministic check.

**Results.**

- **17 of 20** false claims were caught by the **deterministic checks alone** (no model call). The 3 missed are about meaning, so they need the model check: F17 (cause not supported) and F19 and F20 (wrong date).
- **0 of 5** true claims were wrongly rejected.
- The 6 additional claims: 6 of 6 caught by their intended check.
- The first, AI-assisted run of the same set caught 15 of 20 (1 could not be evaluated because of a runtime error). Details below.

This is a small test set of builder-written cases with synthetic articles, run once. It shows what the Evaluator catches and what it misses; it is not a benchmark and not a general accuracy figure.

**Tests.** 331 automated tests pass (`python -m pytest tests`); 2 more integration tests skip when Zetaris or Nightshift are down.

Two evaluations were built to test the "professional scepticism" layer honestly. Both use real runs (traces in `logs/`), no mocks and no cached model replies.
Nightshift 1.2.1 often returned 502 during this work, which limited what could be completed. **All figures below are one run, a small sample, with synthetic company
data and real market data.** Prepared evaluation data is in `data/eval/` (see `data/eval/README_EVAL.md`).

### 1. Planted false-claim test of the Evaluator - completed

`python scripts/eval_planted_claims.py` sends 20 deliberately false or weak claims and 5 correct controls through the same Evaluator the product uses (real Zetaris queries,
real deterministic checks, real Nemotron support check). The articles the claims cite are **synthetic prepared data**; the ECB exchange rates are live.
Full table: `output_examples/eval_planted_claims.md`; trace `logs/run_20261023_220200.jsonl`; results `logs/eval_planted_20261023_220200.jsonl`.

| Result | Count |
|---|---|
| False claims caught | **15 / 20** |
| Correct (control) claims wrongly rejected | **0 / 5** |
| Could not be evaluated because of a runtime error (counted separately, never as caught) | **1** (F19, 502 after retries) |

Caught: wrong FX range and direction (2 of 3), stale sources (2/2), untrusted publishers only (2/2), single source (2/2), copied report from two outlets or the same outlet (2/2),
wrong supplier country (2/2), cause not supported by the cited sources (2/2), and one of the two wrong-date claims.

**Missed (4), as measured:**
- **F03, invented FX numbers:** a claim quoting a 9 % won move whose attached ECB figures it had falsified; the Evaluator trusts the moves attached to a currency claim and the model check judged it consistent. (The cited ECB article does show +9.94 % over 90 days, so the false number happened to sit close to the real one; this is a weak plant.)
- **F04, wrong currency pair:** a South Korean part explained with GBP/JPY data. The numbers matched the yen data, so neither the deterministic check nor the model check noticed the currency was Japan's, not Korea's.
- **F15 and F16, GBP impact that does not match Ganga's purchase data:** the claim text stated a GBP exposure 10x too large (P001) and 10x too small (P002) against Zetaris spend. The Evaluator does not compare GBP figures written inside a claim with Zetaris; Vidhur computes the GBP impact itself from Zetaris after a claim passes, so such a figure never reaches the brief, but the Evaluator did not flag it.

**The AI support check on the meaning-based plants (F17, F19, F20):** the AI support check caught 2/3 in run 20261023_220200: F17 and F20 were caught as `NOT_SUPPORTED` by the model check (`llm_checked: true`); F19 could not be evaluated (502 after retries, `provider_deadline`). The AI support check is designed to catch these semantic failures; it was not evaluated as a separate metric in this 25-case set, and 2/3 is a single run on a small sample.

These are gaps in this version, reported as they came out. The test cases were not changed after seeing results. (F03, F04, F15 and F16 are addressed by the new deterministic checks: see 1b.)

### 1b. Deterministic re-run of the planted claims (no AI) - old vs new

`python scripts/eval_planted_deterministic.py` re-runs the same 25 planted claims (`data/eval/planted_claims.csv`, **not edited**) through the Evaluator's deterministic checks
only: no Nightshift, no model call (Ganga's rows come from `data/purchases.csv`, ECB rates are live). The old figures are the AI-assisted run above. Full table: `output_examples/eval_planted_deterministic.md`.

| Failure type | Planted | Old: caught | Old: missed | Old: not evaluated | New (no AI): caught | New: missed |
|---|---|---|---|---|---|---|
| fabricated number | 1 | 0 | 1 | 0 | 1 | 0 |
| FX mismatch | 2 | 2 | 0 | 0 | 2 | 0 |
| wrong currency | 1 | 0 | 1 | 0 | 1 | 0 |
| GBP mismatch | 2 | 0 | 2 | 0 | 2 | 0 |
| duplicate source | 2 | 2 | 0 | 0 | 2 | 0 |
| stale source | 2 | 2 | 0 | 0 | 2 | 0 |
| other (untrusted publisher, single source, wrong country, cause not supported, wrong date) | 10 | 9 | 0 | 1 | 7 | 3 |
| **All false claims** | **20** | **15** | **4** | **1** | **17** | **3** |

Controls: 0/5 wrongly rejected, as before. The 3 still missed (F17 cause not supported, F19 and F20 wrong dates) are about meaning, so they need the model check; running without AI is
not a substitute for it. The old run had F18 caught by the model; the new run catches it deterministically because "25 percent" is in no cited excerpt.
**6 NEW planted claims**, one per new check, are in `data/eval/planted_claims_new.csv` (marked NEW, with their own articles in `data/eval/planted_sources_new.csv`): all 6 were caught by their
intended check (N01 fabricated number, N02 FX rate, N03 wrong currency, N04 GBP figure, N05 duplicate source, N06 stale source). The only harness change: the old code `NOT_INDEPENDENT` was
renamed `DUPLICATE_SOURCE`. Same limits as above: builder-written cases, one run, synthetic articles.

### 2. Backtest against baselines - FUTURE WORK (not completed, no result claimed)

**Status: future work.** The script exists and is tested, but it never produced a scored result, so nothing here should be read as evidence that Vidhur beats a baseline. `python scripts/eval_backtest.py` compares (a) a naive "always flat" baseline, (b) one Nemotron call with the same inputs and (c) Vidhur, on 5 historical cases, scoring
the direction of Ganga's landed cost in GBP over the next 91 days (up / down / flat within +/-2 %) and whether Vidhur's GBP range contained the real outcome.
Real outcomes use real ECB rates (Frankfurter) and Ganga's synthetic purchase mix. **Taiwan dollar is proxied by USD because the ECB does not publish TWD**
(Taiwan is the GPU chip supplier and Ganga's largest spend, so this matters; Vidhur's own Currency Analyst reports "no data" for TWD).
Vidhur can only see data dated on or before `as_of`; this is enforced in code (`core/asof.py`), not in the prompt.

| Case | as_of | Real outcome | Naive | Model only | Vidhur |
|---|---|---|---|---|---|
| B1 chip shortage | 2021-02-01 | flat (-1.56 %) | flat (right) | not completed (502) | not completed (502) |
| B2 Taiwan Strait tension | 2022-08-02 | up (+3.06 %) | flat (wrong) | not completed (502) | not completed (502) |
| B3 UK mini-budget | 2022-09-23 | not computed | - | not run | interrupted, not scored |
| B4 calmer quarter | 2023-05-02 | not computed | - | not run | not run |
| B5 calmer quarter | 2024-09-02 | not computed | - | not run | not run |

**There is no result for whether Vidhur beats the baselines.** Both completed cases ended "not completed" for the model-based approaches because the runtime answered 502
on every retry (4 attempts, 60 s apart), and the job then hit its time limit. Details: `output_examples/eval_backtest.md`. The script resumes where it stopped with
`--previous logs/backtest_results_20261023.jsonl --only B3,B4,B5`.

### Limits

- Nightshift 1.2.1 returned 502 (`provider_error`: `empty_response`, `provider_deadline`) on a large share of calls; every failure is logged with the routed model (receipt ids are not exposed by the runtime).
- Planted-claim tests (1 and 1b): 25 claims (plus 6 new) written by the builder, one run each. It shows what the Evaluator does and does not catch, not a general accuracy figure.
- Backtest: 5 cases planned, 2 attempted, 0 completed for the model-based approaches. The news set is small (2-7 real headlines per case, CNBC and Fortune only), the model may remember these historical events from training, and the outcome is a currency-driven proxy for landed cost (no freight, duties or part-price changes).
- Company data is synthetic for privacy; News is cached and dated for reproducibility; ECB FX rates are fetched live (historical in the backtest).

## Sample inputs and outputs

`input_examples/input_<time>.json` is saved by `main.py` for each run (the command, flags, lanes and Ganga spend read from Zetaris, no secrets);
`output_examples/brief_<time>.md` is the CFO brief it produced; the trace is `logs/run_<time>.jsonl`. These are real runs; outputs are never edited by hand. **The runs in the table below predate `--demo`, the calculator records and the evidence trail page (older output schema).** The current-schema samples are the `--demo` folders and the live-run folder:

- `output_examples/live_run_20261024_3drivers/` (run `d9decb7d`, 1502 s): 3-driver live run under an overloaded runtime: all three claims verified; two Red Team steps did not complete (pending review). D2 is DECISION CLEARED; D1 and D3 are DECISION PENDING REVIEW with provisional recommendations. See its README. Its brief and evidence trail were re-rendered from the saved world state with the current code (no model calls).
- `output_examples/d3_focused_run_20261024/` (run `334f4e92`, 751 s, 6 model calls ok, 2 failed attempts): D3-only run via `--drivers`. In a focused D3 run, the full Red Team loop closed (3 objections defended in writing, decision cleared). The AI text quotes two premiums (£2,469 and £4,939); the evidence trail and dashboard note that Python calculated £4,939. See its README.
- `output_examples/live_run_20261024_could_not_verify/` (run `5b82b132`, 422 s, 7 model calls, 0 failed): the **honest COULD NOT VERIFY live run**, with its README. D1 (Taiwan) was sent back for `TOO_FEW_SOURCES` and ended Could not verify; D2 (EUR) was verified at High confidence, got 2 Red Team objections that the Decision Maker defended in writing, and ended at no action.
- `output_examples/demo_run_20261024_v3/` (input `input_20261024_153350.json`): the **latest complete `--demo` run** (its evidence trail page was re-rendered from the saved world state after the "What would change this decision" section was added; the brief is as the run wrote it). D1 (Taiwan, geopolitics) was sent back by the Evaluator, passed with Low confidence, and the Red Team's 3 objections moved its 100 % hedge to a 20 % second-supplier shift (worst / likely / best +£160,978 / +£80,489 / +£26,830). D2 (Germany, currency, OBSERVED) is a cost fall (-£8,967 / -£10,446 / -£11,926) and ends at NO_ACTION.
- `output_examples/live_run_20261025_3drivers/` (run `b896719b`): First run where all three Red Team loops finished; D1 ended contested; see Limitations.
- `output_examples/demo_run_20261024_v2/` (input `input_20261024_144545.json`): the current complete `--demo` run, after the currency and hedge-logic fixes. D1 (Taiwan, geopolitics) was sent back once for `TOO_FEW_SOURCES`, passed, and its 100 % hedge was revised by the Red Team loop to a 20 % second-supplier shift. D2 (Germany, currency) uses GBP/EUR on a row Zetaris prices in EUR, shows only the assumption it used (`FX_PASS_THROUGH_PCT`), and ends at NO_ACTION because the move is a cost fall. In that run the live FX call timed out, so the calculation used the rate stored on the Zetaris row and the brief says so.
- `output_examples/demo_run_20261024_could_not_verify/` (input `input_20261024_151250.json`): a `--demo-weak-claim` run kept as the example of the **honest "could not verify" path**: when the evidence could not support a claim after the Evaluator's send-back, the brief says so rather than presenting an unverified figure as a recommendation. Not a complete run.
- `output_examples/demo_run_20261024/`: an earlier complete run, **kept for the record but made before the currency and hedge-logic fixes** (its D2 priced the German part in USD and used GBP/USD against a GBP/EUR claim, and the decision logic could recommend a hedge on a cost fall). Do not cite its D2.

| Input | Output (brief) | What it shows |
|---|---|---|
| `input_20261023_074501.json` (`python main.py`) | `brief_20261023_075606.md` | A normal run, before the Decision Maker existed: Planner's plan, verified claims with worst / likely / best GBP, evidence trail with publisher tiers |
| `input_20261023_081111.json` (`--demo-untrusted-source`) | `brief_20261023_081832.md` | The Evaluator rejecting a first attempt that cited only untrusted sources (`NO_TRUSTED_SOURCE`) and the analyst revising |
| `input_20261023_202729.json` (`--demo-red-team`) | `brief_20261023_211838.md` | **The Red Team loop completing live:** the first proposal was told to hedge 100 %; the Red Team raised 3 objections (one high: concentration), the Decision Maker revised to a 50 % hedge, the Red Team marked the objections resolved. Another decision shows "Red Team review could not run" after repeated 502s. Partial run, labelled as such |
| `input_20261023_212734.json` (`--demo-weak-claim`) | `brief_20261023_220144.md` | The Evaluator sending back a one-source claim (`TOO_FEW_SOURCES`) and the analyst revising. Partial run (502s stopped some work items), labelled as such |

Both 2026-10-23 evening runs went through `--skip-healthcheck` after the healthcheck itself failed on a busy Nightshift 1.2.1 (retried 4 times, 60 s apart, first),
so the command recorded in their input files includes that flag. Traces are `logs/run_20261023_202729.jsonl` and `logs/run_20261023_212734.jsonl`.
Older briefs that said "NOT challenged", or came from runs with no saved input file, were deleted and replaced by the runs above.

## Setup, usage and dependencies

Prerequisites: Docker Desktop with the **Zetaris** platform running (UI http://localhost:3000, API http://localhost:8888) and the **Meterless Nightshift**
app running (http://127.0.0.1:8088/v1) with a lane called `vidhur-analyst`.

Start Zetaris if it is not running: in the folder that holds Zetaris's `docker-compose.yml` (on the build machine, `~/zetaris-platform` inside WSL) run

```bash
docker compose up -d
```

### Local (Windows PowerShell)

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env                     # fill in the Nightshift token and the Zetaris login - never commit .env
python scripts/load_ganga_to_zetaris.py    # once: copies data/*.csv into Zetaris and registers the tables (safe to run twice)
python scripts/healthcheck.py              # ALWAYS run this first
python main.py
```

### Docker

```powershell
docker build -t vidhur .
docker run --rm --env-file .env vidhur python scripts/healthcheck.py
docker run --rm --env-file .env -v "${PWD}/output_examples:/app/output_examples" -v "${PWD}/input_examples:/app/input_examples" -v "${PWD}/logs:/app/logs" vidhur
```

Inside a container `localhost` is the container itself, so Vidhur reaches Nightshift and Zetaris at **`host.docker.internal`**. This is automatic:
the image sets `VIDHUR_IN_DOCKER=1` and `core/config.py` rewrites `localhost` / `127.0.0.1` in `METERLESS_BASE_URL` and `ZETARIS_API_URL` to
`host.docker.internal` (override the name with `DOCKER_HOST_ALIAS`). The same `.env` works locally and in Docker; `.env` is never copied into the image
(`.dockerignore`), so pass it with `--env-file`.

### The healthcheck (and what `main.py` does first)

`python scripts/healthcheck.py` finishes in under 30 s and prints a PASS / FAIL list: required environment variables are set (names only, values are never printed);
**Nightshift** is reachable and the token is valid (one tiny call, it shows which model answered); **Zetaris** is reachable and the `ganga` tables are present.
`main.py` runs the same checks before it starts and stops with a clear message if one fails (`--skip-healthcheck` to bypass).

### If a service is down

- **Zetaris not reachable:** the message tells you to start it (the `docker compose up -d` command above) and the program exits without a traceback.
- **Nightshift busy or offline** (502s, timeouts, connection refused after the automatic retries): when a component times out, Vidhur records an audit warning and marks the step pending review, rather than crashing or showing it as cleared. Whatever completed is saved to the World State and the
  trace, the brief lists the agents that could not run and why, the console prints `AI runtime busy or offline — partial results saved. Try again later.`
  and the exit code is non-zero (`1` = stopped before the run by the healthcheck, `2` = partial results saved).
- A decision whose Red Team call failed is labelled **DECISION PENDING REVIEW** and shown as a provisional recommendation (see "Cleared and pending decisions" above).
  A decision the Red Team did review and raised no high or medium objection to says so, in different words.

## Usage

```powershell
python main.py                            # the full run (15-20 minutes on a healthy runtime)
python main.py --demo                     # FAST DEMO: 3 drivers, cached dated evidence, send-back + Red Team challenge (below)
python main.py --demo-weak-claim          # watch the Evaluator catch a weak claim (below)
python main.py --demo-untrusted-source    # watch it catch a claim backed only by untrusted sources (below)
python main.py --demo-red-team            # watch the Red Team challenge an aggressive decision (below)
python main.py --workers 2                # analysts in parallel (default 3)
python main.py --refresh-news             # ignore the news cache and fetch live
python main.py --skip-healthcheck         # skip the start-up checks
python scripts/healthcheck.py             # PASS / FAIL list for Nightshift, Zetaris, env vars
python scripts/check_lanes.py             # which Nemotron model answers each Nightshift lane (3 calls per lane)
python -m pytest tests                    # all tests; integration tests skip if Zetaris / Nightshift are down
```

Env (see `.env.example`): `PLANNER_MAX_DELEGATIONS` (default 4), `LLM_MAX_ATTEMPTS` (default 5), `EVIDENCE_MAX_AGE_DAYS` (default 30).
Decision premiums: `data/decision_assumptions.csv`.

### `--demo` (fast demo mode)

One company run limited to three drivers: D1 the Taiwan GPU chip (geopolitics), D2 the largest-spend part whose currency has ECB data
(the euro-supplied cooling loop; currency) and D3 the South Korean HBM memory stack (supplier & commodity; omitted if Ganga buys no memory part). What is different, and nothing else is:

- **Cached, dated evidence.** The news comes from `data/demo_evidence/` (saved by `python scripts/save_demo_evidence.py` from a real live fetch; `manifest.json`
  holds the date). The brief and the evidence page say "News is cached and dated for reproducibility (cached <date>); ECB FX rates are fetched live". **FX stays live** (Frankfurter / ECB). The Evaluator judges the cached
  articles' freshness as of the cache date, so the demo keeps working later.
- **Fixed plan, no Planner call.** The three drivers are picked by a documented rule (`core/demo.py`), so the Planner model is not called in demo mode.
- **Both demo instructions on.** The Geopolitics Analyst's first attempt cites one source (the Evaluator sends it back), and the first decision is told to be
  aggressive (the Red Team challenges it and writes its reason). Real model calls, real checks; the flags are printed and recorded in the brief.
- **Bounded AI calls.** 180 s timeout per call in demo mode, 3 attempts, a 25-minute time budget for the whole run (no call starts after it), and the model's long private
  reasoning switched off through Nightshift (`chat_template_kwargs.enable_thinking=false`). Demo mode only: normal runs and the evaluations are unchanged.
- **If a call still fails, the driver says so.** It is labelled **Could not verify** in the brief, the console and the evidence page, with the reason and
  "no GBP figure - this is NOT zero impact", and the run continues.

### The evidence trail page

Every run (not only `--demo`) writes `output/evidence_trail.html` (`core/evidence_page.py`; one self-contained file, no scripts, no external services,
light and dark). Top: the CFO summary (drivers, worst / likely / best, confidence, recommendation). Then, per driver: claim -> sources (publisher, tier, date, link)
-> deterministic check results -> Evaluator verdict and any send-back -> Red Team challenge and reason -> final revision -> the GBP calculation record. Each decision (and each Could not verify driver) ends with a **What would change this decision** section.
The page is built from the same world-state report as the brief. `output/` is git-ignored; a copy of a run's page is saved next to its brief when it is kept as a sample.

### `--demo-weak-claim` (a demo flag, not hidden faking)

With this flag the **Geopolitics Analyst's first attempt** gets one extra instruction: cite exactly one source. The model call is real and
the Evaluator's check is real; only the analyst's instruction changes. The Evaluator then sends the claim back (`TOO_FEW_SOURCES`), the analyst
revises it with the feedback, and the brief shows the full audit history. The flag is printed in the console, recorded in the trace
(`demo_weak_claim: true`) and stated at the top of the brief. (The Currency Analyst always cites the FX data plus news, so the flag does not affect it.)

### `--demo-red-team` (a demo flag, not hidden faking)

With this flag the **first proposal for the lowest-confidence verified claim** gets one extra instruction: hedge 100% of that part's spend.
The Decision Maker, the Red Team and the loop are real model calls with real validation; the Red Team decides what to object to, and the Decision
Maker decides whether to revise or defend. The run ends with the decision revised, defended or `CONTESTED`. The flag is printed, recorded in the trace
(`demo_aggressive: true`) and stated at the top of the brief.

### `--demo-untrusted-source` (a demo flag, not hidden faking)

With this flag the **Geopolitics Analyst's first attempt** is shown the *least-trusted* articles first (Tier 3 before Tier 1) and is told to
cite only the first two. The model call and the Evaluator's check are real; only the evidence offered to the analyst and the instruction change.
The Evaluator sends the claim back (`NO_TRUSTED_SOURCE`, and `SOURCES_NOT_INDEPENDENT` if the two share a publisher), the analyst revises
from the normal trust-ranked evidence, and the claim passes on retry or ends `UNVERIFIED`. The flag is printed, recorded in the trace
(`demo_untrusted_source: true`) and stated at the top of the brief.

**Dependencies** (`requirements.txt`): `openai`, `python-dotenv`, `pandas`, `requests`, `pytest`. Services: Docker Desktop with Zetaris, and Meterless Nightshift (see Prerequisites above).

## Where things are

| Path | What |
|---|---|
| `agents/planner.py` | Planner (Vidhur): plan, delegations with reasons, review and one re-delegation |
| `agents/geopolitics_analyst.py`, `agents/currency_analyst.py`, `agents/supplier_commodity.py` | The three analysts (the third reuses the geopolitics pipeline with its own prompt, query and keywords) |
| `agents/evaluator.py` | The Evaluator and the send-back loop |
| `agents/decision_maker.py`, `core/red_team.py`, `core/decisions.py` | Decision Maker, Red Team, and the option / GBP maths (Python) |
| `agents/workflow.py` | One work item: analyst -> Evaluator loop -> GBP impact |
| `engines/world_model/` | Meterless World Model engine |
| `core/shared_state.py` | Vidhur's use of the world model (what each agent writes and reads) |
| `scripts/healthcheck.py`, `core/preflight.py`, `core/failures.py` | Start-up checks (PASS / FAIL) and the "agents that could not run" report |
| `core/zetaris_client.py` | Zetaris REST client - one Lightning SQL statement per request |
| `core/llm.py` | Meterless Nightshift client (OpenAI-compatible), retries, pin tracking |
| `core/trace.py` | Append-only JSONL trace (agent, target, model, tokens, latency, sources, decision) |
| `core/news.py`, `core/fx.py` | Real evidence: RSS news and ECB exchange rates; trust-aware ranking |
| `core/source_trust.py`, `data/source_trust.csv` | Publisher trust tiers (curated list) |
| `scripts/check_lanes.py` | Calls each Nightshift lane 3 times and prints which model answered |
| `scripts/eval_planted_claims.py` | Planted false-claim test of the Evaluator (20 false + 5 correct claims) -> `output_examples/eval_planted_claims.md` |
| `scripts/eval_backtest.py` | 5-case backtest of Vidhur vs a naive baseline and a model-only call -> `output_examples/eval_backtest.md` |
| `core/asof.py` | Point-in-time guards for the backtest: news, FX window and Zetaris quarters after `as_of` raise `AsOfViolation` |
| `data/eval/` | Prepared evaluation data (planted claims, synthetic planted articles, backtest cases, real dated backtest headlines); see `data/eval/README_EVAL.md` |
| `core/calculator.py` | **All GBP figures**: baseline, scenario range, calculation record, OBSERVED / FORECAST / SCENARIO labels |
| `core/impact.py`, `core/brief.py` | Impact record and confidence; the CFO brief |
| `core/next_steps.py` | "What would change this decision": confidence meaning, what would raise it, re-run trigger (plain Python, no model) |
| `scripts/eval_planted_deterministic.py` | Planted claims through the deterministic checks only (no AI) -> `output_examples/eval_planted_deterministic.md` |
| `scripts/load_ganga_to_zetaris.py` | Registers Ganga's CSVs as Zetaris tables |
| `docs/meterless_engine.md` | The World Model spec we followed and the test results |

## Notes on the AI runtime

All product AI calls go through Meterless Nightshift, and every AI call is Nvidia Nemotron. **All agents (Planner, all three analysts, Evaluator) use the
lane `vidhur-analyst`** (`nvidia/nemotron-3.5-lightning-30b-a3b`); see Known limitations for why. `.env` holds the lane to request (`MODEL_*`) and the
Nemotron model that must answer (`MODEL_*_EXPECTED`). **Every AI call line in the trace records `requested_lane`, `answered_model` and `pin_honoured`**
(failed attempts record `answered_model: null`). `python scripts/check_lanes.py` re-tests the lanes. The runtime enforces a ~45 s per-request deadline
and answers 502 when a long reasoning call overruns it, so the client retries automatically (`LLM_MAX_ATTEMPTS`) and logs every failed attempt.

## Credits

- Zetaris (data layer), Meterless (World Model spec, Nightshift runtime) and NVIDIA (Nemotron) technology as described above.
- Built for the Open Agent Hackathon 2026 with Claude Code.

## Limitations

- **Nemotron Super is not available on this account (404).** Checked 2026-10-24 on Nightshift 1.2.3: `nvidia/nemotron-3-super-120b-a12b` answers 502 `nvidia_404 ... Function not found for account` and `nvidia/llama-3.3-nemotron-super-49b-v1.5` answers 410 Gone. `vidhur-evaluator` still resolves to Lightning, so every agent runs on Lightning (`vidhur-analyst`). That means **no cross-model check: the same model drafts, audits and challenges.** The separation is of roles and prompts, not of models. If a larger lane is fixed, set `MODEL_EVALUATOR` / `MODEL_EVALUATOR_EXPECTED` in `.env`.
- **Live runs are slow and the runtime is flaky.** Demo runs took 341-990 s (about 6-16 minutes); full runs take 15-20 minutes on a healthy runtime and longer when it is busy, because there are many sequential model calls against a per-request deadline of about 45 s. Nightshift 502s (`provider_error`, `empty_response`, `provider_deadline`) can stop a step. Vidhur then retries with bounded attempts and timeouts, finishes with **partial results labelled honestly** ("Red Team review could not run", "Could not verify"), saves what completed, and exits non-zero (`2`). The JSONL trace shows where it failed. Several kept sample runs are partial for this reason and are marked so.
- **TWD is proxied by USD.** The ECB does not publish a Taiwan dollar rate. Zetaris prices the Taiwan GPU in USD, and the backtest uses USD as the proxy for TWD; Vidhur's own Currency Analyst reports "no data" for TWD and is skipped for that part. A production version would use a treasury or FX provider with direct TWD rates (not built).
- **Freight is left out.** There is no freight analyst; Ganga's high-value parts mostly ship by air, so freight is a small share of landed cost, but it is not modelled. Duties and part-price changes are not modelled either.
- **Two analysts today.** Geopolitics and currency only. Commodity, logistics, trade and supplier-health analysts are not built.
- **The deterministic checks miss 3 of 20 planted claims** (cause not supported, F17; wrong date, F19 and F20). Those need the model check, which is itself one small model; in the recorded AI-assisted run it caught 2 of the 3 (F17, F20) and F19 could not be evaluated, so no 3/3 or 20/20 is claimed.
- **The synthetic data is declared.** Ganga AI Solutions and its purchase history are made up; news and FX are real.
- **The backtest is future work.** It produced no scored result (see Evaluation); no claim is made that Vidhur beats a baseline.
- **Decision premiums are assumptions** (`data/decision_assumptions.csv`), not market quotes, and the second supplier is assumed to be unexposed to the claim's driver. Decisions are made per claim, so two claims on one part are not combined. The re-run trigger is advice, not a monitor.
- **The start-up healthcheck refuses to start while Nightshift is answering 5xx.** Use `--skip-healthcheck` to run anyway; Vidhur then retries inside the run and finishes with partial results if the runtime stays down.
- The Decision Maker and Red Team replies are written by the AI and can be wrong. In run b896719b (25 Oct), the Decision Maker defended the Taiwan (D1) and Korean memory (D3) decisions by citing ECB exchange rate data, which does not support those claims, and the Red Team accepted that reason for D3. Python checks the claims and the money figures, but not the logic of the debate replies.
- AI-written rationale text can quote money figures that differ from Python's. In the same run, D1's rationale mentioned a 50% hedge and a worst case of £90,550 while proposing 100%, and Python's worst case was £160,978. The Python figures in the CFO brief are the ones to use.
- Results vary between runs. The D1 decision changed from a 100% hedge to a 20% second-supplier shift in run v3, and stayed contested at 100% (sent to the CFO for judgement) in run b896719b.
