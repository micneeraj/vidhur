"""Geopolitics Analyst: turns real news into structured, evidence-backed cost claims."""

from core import config, llm
from core.claims import Claim
from core.news import fetch_news

MAX_ARTICLES_SHOWN = 15

# --demo-weak-claim: appended to the analyst's FIRST attempt only. The model call and the
# Evaluator check are both real; only this instruction differs.
DEMO_WEAK_CLAIM_INSTRUCTION = (
    "DEMO MODE (deliberate weak-claim test, requested by the operator): in source_ids list exactly ONE "
    "article id, even if several articles support the claim."
)

SYSTEM_PROMPT = (
    "You are the Geopolitics Analyst for Ganga AI Solutions, a UK maker of GPU AI servers. "
    "You assess how geopolitical news (export controls, sanctions, tariffs, tensions, trade deals) "
    "could change the cost of ONE part bought from ONE supplier country. "
    "Use ONLY the numbered articles you are given. Never invent facts, articles or ids. "
    "Reply with a single JSON object and nothing else."
)

CLAIM_FORMAT = """Reply with exactly this JSON shape:
{"claims": [{"claim": "<one or two sentences: what is happening and how it affects the cost of this part>",
  "part_id": "<the part id above>", "supplier_country": "<the country above>",
  "direction": "cost_up" or "cost_down",
  "pct_low": <number>, "pct_high": <number>,
  "source_ids": ["A1", "A2"],
  "reasoning": "<why these articles support the claim and why this % range>"}]}
Rules: pct_low and pct_high are your own estimate of the % change in the part's unit cost over the next
two quarters (positive numbers, pct_low <= pct_high, a plausible range). Cite at least two articles from
different publishers whenever the articles allow it. If the articles do not show a development that
plausibly affects this part from this country, return {"claims": []}.
Scope: the driver of the claim must be a geopolitical or trade-policy development (export controls, sanctions,
tariffs, diplomatic or military tensions, trade deals, state subsidies). General market demand, energy prices,
commodity prices and currency moves belong to other analysts - never base a claim on them."""


class GeopoliticsAnalyst:
    name = "geopolitics"

    def __init__(self, trace, model=None, demo_weak_claim=False, refresh_news=False):
        self.trace = trace
        self.model = model or config.analyst_model()
        self.expected_model = config.analyst_expected() if model is None else model
        self.demo_weak_claim = demo_weak_claim
        self.refresh_news = refresh_news
        self.pool = {}  # url -> Article: every article fetched this run (the Evaluator checks against it)
        self._articles = {}  # part_id -> list[Article] shown to the model for that target

    # ------------------------------------------------------------------ news
    def _query(self, target, broaden=False):
        if broaden:
            return f"{target.country} {target.category} supply chain"
        return f"{target.country} {target.category} export controls OR sanctions OR tariffs OR tensions"

    def _research(self, target, broaden=False):
        query = self._query(target, broaden)
        articles, info = fetch_news(query, refresh=self.refresh_news)
        for a in articles:
            self.pool[a.url] = a
        self.trace.event(
            self.name,
            "news:fetch",
            input_summary=query,
            output_summary=f"{len(articles)} articles ({'cache' if info['from_cache'] else 'live'}, fetched {info['fetched_at']})"
            + (f"; feed errors: {info['errors']}" if info["errors"] else ""),
            sources=[a.url for a in articles[:5]],
        )
        known = self._articles.setdefault(target.part_id, [])
        have = {a.url for a in known}
        known.extend(a for a in articles if a.url not in have)
        known.sort(key=lambda a: a.published, reverse=True)
        return known

    @staticmethod
    def _numbered(articles):
        shown = articles[:MAX_ARTICLES_SHOWN]
        lines = []
        for i, a in enumerate(shown, 1):
            summary = f" - {a.summary[:200]}" if a.summary else ""
            lines.append(f"A{i} | {a.published[:10]} | {a.publisher} | {a.title}{summary}")
        return shown, "\n".join(lines)

    # ------------------------------------------------------------------ claims
    def draft_claim(self, target, claim_id):
        articles = self._research(target)
        return self._ask(target, claim_id, articles, attempt=1, previous=None, feedback=None)

    def revise_claim(self, target, claim, feedback):
        """Second chance after the Evaluator sent the claim back. Searches wider news first."""
        articles = self._research(target, broaden=True)
        return self._ask(target, claim.claim_id, articles, attempt=claim.attempt + 1, previous=claim, feedback=feedback)

    def _ask(self, target, claim_id, articles, attempt, previous, feedback):
        shown, listing = self._numbered(articles)
        if not shown:
            self.trace.event(self.name, "claim:none", output_summary="no recent articles found", retry_count=attempt - 1)
            return None

        header = (
            f"Part: {target.part_id} {target.part_name} ({target.category}), supplied from {target.country} "
            f"by {target.supplier_name}. Ganga spent GBP {target.quarterly_spend_gbp:,.0f} on it in {target.quarter}.\n\n"
            f"Articles (newest first):\n{listing}\n\n"
        )
        if previous is None:
            task = "Write the cost claim for this part and country.\n" + CLAIM_FORMAT
            if self.demo_weak_claim:
                task += "\n" + DEMO_WEAK_CLAIM_INSTRUCTION
        else:
            task = (
                "Your previous claim was sent back by the Evaluator (the auditor).\n"
                f"Previous claim: {previous.claim}\n"
                f"Evaluator feedback: {' | '.join(feedback)}\n"
                "Revise the claim so it fixes every point of feedback. Use only the articles above.\n" + CLAIM_FORMAT
            )

        result = llm.chat(
            [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": header + task}],
            model=self.model,
            expected_model=self.expected_model,
            agent=self.name,
            step="claim" if previous is None else "revise",
            trace=self.trace,
            retry_count=attempt - 1,
        )
        try:
            data = llm.extract_json(result.text)
            raw = (data.get("claims") or [None])[0]
        except ValueError:
            raw = None
        if not raw:
            self.trace.event(self.name, "claim:none", output_summary="analyst returned no claim", model=result.model, retry_count=attempt - 1)
            return None

        by_id = {f"A{i}": a for i, a in enumerate(shown, 1)}
        # An id the model made up is kept as-is so the Evaluator catches it (it is not in the fetched set).
        urls = [by_id[s].url if s in by_id else str(s) for s in raw.get("source_ids", [])]
        claim = Claim(
            claim_id=claim_id,
            claim=str(raw.get("claim", "")).strip(),
            part_id=str(raw.get("part_id") or target.part_id),
            part_name=target.part_name,
            supplier_country=str(raw.get("supplier_country") or target.country),
            direction=str(raw.get("direction", "")),
            pct_low=_number(raw.get("pct_low")),
            pct_high=_number(raw.get("pct_high")),
            source_urls=urls,
            reasoning=str(raw.get("reasoning", "")).strip(),
            attempt=attempt,
        )
        self.trace.event(
            self.name,
            "claim:draft" if previous is None else "claim:revised",
            input_summary=f"{target.part_id} / {target.country}",
            output_summary=f"{claim.direction} {claim.pct_low}-{claim.pct_high}%: {claim.claim}",
            model=result.model,
            sources=claim.source_urls,
            retry_count=attempt - 1,
            demo_weak_claim=bool(self.demo_weak_claim and previous is None),
        )
        return claim


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")
