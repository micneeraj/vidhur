# source_trust.csv

A **curated, editable config** - not a ground truth. Each row maps a publisher's web domain to a trust tier:

| Tier | Score | Meaning |
|---|---|---|
| 1 | 1.0 | Wire services, top business press, and official sources (central banks, statistics offices, WTO / IMF / World Bank, semiconductor trade bodies) |
| 2 | 0.7 | Well-known general, business and trade press |
| 3 | 0.3 | Anything not listed (the default) |

Add or change rows freely; subdomains match automatically (`uk.reuters.com` counts as `reuters.com`). Matching by
`publisher` name is the fallback when a URL is an aggregator link (Google News). Used by `core/source_trust.py`
