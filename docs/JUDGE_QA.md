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
(for example NOT_CHALLENGED) instead of being passed off as done. In our demo runs one driver was withdrawn this way on three of the
earlier attempts.

**Why multiple agents?**
Separation of duties. The analyst that proposes a claim is not the one that audits it (Evaluator), and the decision is attacked by a third
agent (Red Team) before the CFO sees it. Each hand-off is a written record in the shared World State. Honest limit: all agents run on the same
model (Nemotron Lightning), so this separates roles and prompts, not independent model judgement.

**Why trust this over a spreadsheet?**
A spreadsheet holds numbers and cannot tell you whether the story behind them is supported. Vidhur ties a news event to Ganga's own spend, refuses
to put a number on an unsupported claim, shows the calculation and the sources behind each figure, and records the objections to its own
recommendation. It does not replace the spreadsheet: the arithmetic is the same kind a spreadsheet does, done in Python. What it adds is the
evidence check and the challenge. Limits: synthetic company data, two analysts of six, premiums are assumptions, and the backtest against
baselines is future work, so we make no accuracy claim.
