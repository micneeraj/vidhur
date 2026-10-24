# Judge Q&A

**How are sources checked as independent?**
In code, before any model opinion (`agents/evaluator.py`, `independent_groups`). Two sources count as one if they come from the same outlet,
the same original outlet ("via X"), carry the same headline, or have a near-identical excerpt. A claim needs at least 2 independent fresh sources,
at least one from a Tier 1 or 2 publisher (`core/source_trust.py`), and none older than `EVIDENCE_MAX_AGE_DAYS` (default 30). Failures are
`DUPLICATE_SOURCE`, `SOURCES_NOT_INDEPENDENT`, `NO_TRUSTED_SOURCE`, `STALE_SOURCE`. Limit: this catches syndication we can see, not
two outlets that quietly share one unseen origin.

**How is the GBP number proven correct?**
The model never writes a GBP figure. `core/calculator.py` computes it from Ganga's Zetaris purchase rows, an ECB FX rate with its date, and the
explicit assumptions in `data/decision_assumptions.csv`. Every figure in the brief has a calculation record and a label: OBSERVED (read from data),
FORECAST (a percentage a cited source prints) or SCENARIO (our assumption). The Evaluator re-checks any number in a claim against the cited
excerpt, the real FX rate and the calculator (`NUMBER_NOT_IN_SOURCE`, `FX_MISMATCH`, `GBP_MISMATCH`). Worst / likely / best is a scenario range,
not a probability. "Correct" means arithmetic on the stated inputs, not that the forecast will come true. Tests cover the arithmetic and the checks.

**What happens if a source is missing?**
The claim goes back to the analyst with the reason (max 2 send-backs). If it still lacks independent, fresh, trusted support, it is shown as
"Could not verify" with no GBP figure, and the brief says that is not zero impact. If the model runtime is down, the step is labelled as not run
(for example NOT_CHALLENGED) instead of being passed off as done. In the kept live run (`output_examples/live_run_20261024_could_not_verify/`) the Taiwan driver ended this way.

**Why multiple agents?**
Separation of duties. The analyst that proposes a claim is not the one that audits it (Evaluator), and the decision is attacked by a third
agent (Red Team) before the CFO sees it. Each hand-off is a written record in the shared World State. Honest limit: all agents run on the same
model (Nemotron Lightning), so this separates roles and prompts, not independent model judgement.

**Why trust this over a spreadsheet?**
A spreadsheet holds numbers and cannot tell you whether the story behind them is supported. Vidhur ties a news event to Ganga's own spend, refuses
to put a number on an unsupported claim, shows the calculation and the sources behind each figure, and records the objections to its own
recommendation. It does not replace the spreadsheet: the arithmetic is the same kind a spreadsheet does, done in Python. What it adds is the
evidence check and the challenge. Limits: synthetic company data, three analysts, premiums are assumptions, and the backtest against
baselines is future work, so we make no accuracy claim.

**Why use this instead of an analyst?**
Before: an analyst checks the news, FX and supplier exposure by hand, builds a spreadsheet and spends hours defending the assumptions. After: Vidhur
produces a ranked cost-impact brief with citations, a calculation record for every figure, the Red Team's objections and a recommended action,
in minutes (a live `--demo` run took 341-990 s, about 6-16 minutes). The point is traceability, not replacing the analyst: every GBP figure can be re-derived
from the input rows, FX rate and date, formula and assumptions shown in the brief, every claim shows the sources and tiers behind it, and every
recommendation shows what was objected to and why it stood or changed. A person still decides.

**Why does a live run take 7-12 minutes?**
Speed is traded for verification: analysts run in parallel, claims are cross-checked against Zetaris with SQL, the deterministic checks run before any model
opinion, and the Decision Maker and Red Team loop through up to 2 challenge rounds. Observed demo-run times were 341 to 990 s (about 6-16 minutes; 422, 738 and 905 s for the runs kept as samples), so "7-12 minutes" is a common figure, not
a guarantee; a full run is 15-20 minutes on a healthy runtime. Demo mode uses cached, dated news evidence so the run is reproducible (FX stays live). We do not
claim zero hallucination. On our planted-claims set the deterministic checks caught 17 of 20 false claims, and **3 of 20 still slipped through**: one
"cause not supported" claim (F17) and two "wrong date" claims (F19, F20). Those are about meaning, so they depend on the model check. The set is small,
builder-written and run once; it is not a benchmark.

**TWD: why a USD proxy?**
The ECB does not publish a Taiwan dollar rate, so there is no TWD series to compute a move from. Zetaris prices the Taiwan GPU in USD, the Currency Analyst
reports "no data" for TWD and is skipped for that part, and the backtest uses USD as a proxy. In production this would use a treasury or FX provider with
direct TWD rates. That is not built.

**What if Nightshift returns 502 mid-run?**
Calls are retried a bounded number of times (`LLM_MAX_ATTEMPTS`) with timeouts, and the run continues with the steps that can still run. Whatever fails is
labelled instead of being passed off as done: a driver whose agent failed is "Could not verify" (no GBP figure, not zero impact), and a decision whose Red Team
call failed says "Red Team review could not run - treat this recommendation as unreviewed". The brief opens with a partial-results banner and lists the agents
that could not run and why, the run exits with code 2, and the JSONL trace (`logs/run_*.jsonl`) shows where each failure happened. Earlier runs on the busy
runtime ended partial this way and are marked as such.

**Low versus High confidence: what do I trust?**
Confidence reflects evidence quality and source agreement, not the size of the impact. Low = investigate before committing money; High = fine for routine
action or monitoring. It is computed in Python from the Evaluator result: the number of independent sources, whether the claim passed only after a send-back,
and whether the model judged the support weak. Each decision also says what would raise its confidence (for example a second independent Tier 1 source) and
when to re-run (a currency move beyond `RERUN_FX_MOVE_PCT` from the rate used, or new Tier 1/2 news). A large GBP figure with Low confidence is a prompt to
investigate, not a number to act on.

**Is this just two scripts?**
No: three analysts today (geopolitics, currency, and a supplier & commodity analyst for the South Korean HBM memory part). In a normal run the Planner
(a model call, validated in Python) chooses which analyst works on which part from Ganga's spend in Zetaris and gives a reason for each, or for skipping; in
`--demo` the three drivers (D1 Taiwan GPU, D2 EUR cooling, D3 Korea memory) are fixed by a documented rule. Two feedback loops sit on top: the Evaluator send-back loop
(the analyst must fix a claim or give up) and the Decision Maker and Red Team challenge loop (objections must cite evidence; a revision needs a written reason).
All agents share one event-sourced World State. Adding an analyst is a small registration (write the class, register it in `agents/planner.py` and `main.py`); the third one was added that way and reuses the
news-evidence pipeline and the Evaluator unchanged. It is not zero code, and no freight analyst is built.

**Why the same 2-12% band for memory?**
2-12% is Ganga's default SCENARIO band for a verified directional cost driver with no directly comparable, quantified forecast for this part and quarter. It is not a probability and is not part-specific. No number without evidence: no cited article prints a part-specific quarterly price forecast for the HBM stack, so we do not invent one for memory. A memory-specific band will be added once it is anchored to a cited, part-relevant forecast.

**Does Vidhur ever recommend a hedge or price lock when costs are falling?**
No: no hedge or price lock when the verified expected direction is cost DOWN. A deterministic rule (`decisions.rule_choice`) fixes the decision to no action / monitor before any model is asked. It is not a blanket ban on protection: against a verified cost rise the Decision Maker may propose one.

**What does a price lock cost?**
Not quantified. No price-lock premium is documented, so Vidhur shows "Cost of action: Not quantified - requires a supplier quote for the price-lock premium and volume commitment." and the risk the lock protects (the worst case times the locked share, calculated in Python), the trade-off (savings may be forgone if prices soften) and what would change the recommendation. Hedge and second-supplier premiums are documented SCENARIO assumptions.

**Why was D3 chosen, and how far did it get?**
D3 is selected by materiality (Korean HBM memory, 13% of spend) because memory is a distinct cost mechanism - industry supply and wafer allocation - rather than a repeat of geopolitical or FX analysis. Three drivers cover about 84% of spend. D3 completed claim verification and the GBP scenario calculation; its Red Team step hit the runtime budget in the saved live run. The Red Team logic is implemented and tested; a completed D3 Red Team run is next work. In the brief and dashboard: "Memory exposure is driven by industry capacity allocation and HBM supply tightness, separate from the Taiwan export-control and FX drivers."
