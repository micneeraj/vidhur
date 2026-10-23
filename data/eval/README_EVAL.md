# Evaluation data (prepared)

Everything in this folder is **prepared evaluation data**, written for the two evaluation scripts. It is not product input.

| File | What it is |
|---|---|
| `planted_claims.csv` | 20 deliberately false or weak claims and 5 correct controls, each with a label of what is wrong. Written by the builder. |
| `planted_sources.csv` | **Synthetic** articles the planted claims cite (publisher names are used only so the trust list can rank them; they are not real news). Dates are "days old" relative to the run. |
| `backtest_cases.csv` | 5 historical cases (as_of dates) for the backtest. |
| `backtest_news.csv` | **Real** headlines with their real URLs, dated on or before each case's as_of. Dates come from the date in the article URL; titles are as returned by a search index (one is lower-case, kept as returned). Only CNBC and Fortune URLs that carry a date were used because article pages could not be fetched to confirm dates. |

Company data (Ganga AI Solutions) is synthetic; exchange rates (Frankfurter / ECB) are real and historical for the backtest.
Backtest outcomes are NOT stored: they are computed in Python (`scripts/eval_backtest.py`) from real ECB rates and Ganga's purchase mix at run time.
