"""Geopolitics Analyst: turns real news into one structured, evidence-backed cost claim per target."""

from agents.analyst_base import AnalystBase, number
from core.claims import Claim
from core.news import fetch_news, rank_articles

MAX_ARTICLES_SHOWN = 8  # most relevant articles only, pre-ranked in Python

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

CLAIM_FORMAT = """Reply with exactly this JSON (keep claim to 1-2 sentences and reasoning to 1-2 sentences):
{"claims": [{"claim": "...", "part_id": "<part id above>", "supplier_country": "<country above>",
  "direction": "cost_up" or "cost_down", "pct_low": <number>, "pct_high": <number>,
  "source_ids": ["A1", "A2"], "reasoning": "..."}]}
Rules: pct_low and pct_high are your own estimate of the % change in this part's unit cost over the next two
quarters (positive numbers, pct_low <= pct_high). Cite at least two articles from different publishers whenever
the articles allow it. The driver must be a geopolitical or trade-policy development (export controls, sanctions,
tariffs, tensions, trade deals, state subsidies); market demand, energy or commodity prices and currency moves
belong to other analysts - never base a claim on them. If the articles show no such development affecting this
part from this country, reply {"claims": []}."""


class GeopoliticsAnalyst(AnalystBase):
    name = "geopolitics"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._candidates = {}  # work_id -> list[Article] gathered so far

    # ------------------------------------------------------------------ news
    @staticmethod
    def _query(target, broaden=False):
        if broaden:
            return f"{target.country} {target.category} supply chain"
        return f"{target.country} {target.category} export controls OR sanctions OR tariffs OR tensions"

    def _research(self, target, work_id, broaden=False):
        query = self._query(target, broaden)
        articles, info = fetch_news(query, refresh=self.refresh_news)
        self._add_to_pool(articles)
        self.world.record_articles(articles, target.part_name, target.country, self.name)
        self.trace.event(
            self.name, "news:fetch", input_summary=query,
            output_summary=f"{len(articles)} articles ({'cache' if info['from_cache'] else 'live'}, fetched {info['fetched_at']})"
            + (f"; feed errors: {info['errors']}" if info["errors"] else ""),
            sources=[a.url for a in articles[:5]],
        )
        known = self._candidates.setdefault(work_id, [])
        have = {a.url for a in known}
        known.extend(a for a in articles if a.url not in have)
        keywords = [([target.country], 3), (target.category.split() + target.part_name.split()[1:3], 2),
                    (["export", "sanction", "tariff", "control", "ban", "tension", "trade", "restrict", "subsid", "chip"], 1)]
        return rank_articles(known, keywords, k=MAX_ARTICLES_SHOWN)

    @staticmethod
    def _numbered(articles):
        lines = []
        for i, a in enumerate(articles, 1):
            summary = f" - {a.summary[:160]}" if a.summary else ""
            lines.append(f"A{i} | {a.published[:10]} | {a.publisher} | {a.title}{summary}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ claims
    def draft(self, target, work_id):
        shown = self._research(target, work_id)
        return self._ask(target, work_id, shown, attempt=1, feedback=None)

    def revise(self, target, work_id):
        """Second chance after the Evaluator sent the claim back. The feedback is READ from the world state."""
        previous_key, feedback = self.feedback_for(work_id)
        previous = self.world.get_claim(previous_key)
        shown = self._research(target, work_id, broaden=True)
        return self._ask(target, work_id, shown, attempt=previous.attempt + 1, feedback=feedback, previous=previous)

    def _ask(self, target, work_id, shown, attempt, feedback, previous=None):
        if not shown:
            self.trace.event(self.name, "claim:none", output_summary="no recent articles found", retry_count=attempt - 1)
            return None
        header = (f"Part: {target.part_id} {target.part_name} ({target.category}), supplied from {target.country} by "
                  f"{target.supplier_name}. Ganga spent GBP {target.quarterly_spend_gbp:,.0f} on it in {target.quarter}.\n\n"
                  f"Articles (most relevant first):\n{self._numbered(shown)}\n\n")
        if previous is None:
            task = "Write the cost claim for this part and country.\n" + CLAIM_FORMAT
            if self.demo_weak_claim:
                task += "\n" + DEMO_WEAK_CLAIM_INSTRUCTION
        else:
            task = (f"Your previous claim was sent back by the Evaluator (the auditor).\nPrevious claim: {previous.claim}\n"
                    f"Evaluator feedback: {' | '.join(feedback)}\nRevise the claim so it fixes every point of feedback. "
                    "Use only the articles above.\n" + CLAIM_FORMAT)

        data, result = self._ask_json(SYSTEM_PROMPT, header + task, "claim" if previous is None else "revise", attempt - 1)
        raw = ((data or {}).get("claims") or [None])[0]
        if not raw:
            self.trace.event(self.name, "claim:none", output_summary="analyst returned no claim", model=result.model,
                             retry_count=attempt - 1)
            return None

        by_id = {f"A{i}": a for i, a in enumerate(shown, 1)}
        # An id the model made up is kept as-is so the Evaluator catches it (it is not in the fetched set).
        urls = [by_id[s].url if s in by_id else str(s) for s in raw.get("source_ids", [])]
        claim = Claim(
            claim_id=work_id, claim=str(raw.get("claim", "")).strip(),
            part_id=str(raw.get("part_id") or target.part_id), part_name=target.part_name,
            supplier_country=str(raw.get("supplier_country") or target.country), direction=str(raw.get("direction", "")),
            pct_low=number(raw.get("pct_low")), pct_high=number(raw.get("pct_high")), source_urls=urls,
            reasoning=str(raw.get("reasoning", "")).strip(), attempt=attempt, analyst=self.name,
        )
        self.world.write_claim(claim, work_id, self.name)
        self.trace.event(
            self.name, "claim:draft" if previous is None else "claim:revised", input_summary=f"{target.part_id} / {target.country}",
            output_summary=f"{claim.direction} {claim.pct_low}-{claim.pct_high}%: {claim.claim}", model=result.model,
            sources=claim.source_urls, retry_count=attempt - 1, demo_weak_claim=bool(self.demo_weak_claim and previous is None),
        )
        return claim
