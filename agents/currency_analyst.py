"""Currency Analyst: GBP exchange-rate risk from real ECB data. The numbers are computed in Python; the model only explains them.

For each target it looks at the currency of the supplier's country (Taiwan's dollar is not published by the ECB,
so Taiwan is reported as "no data" rather than guessed). The estimated % range is the 30-day and 90-day move of
that currency against the pound, from Frankfurter; the claim assumes the move passes through to Ganga's cost
in full (an upper-bound assumption, stated in every claim).
"""

from agents.analyst_base import AnalystBase
from core import fx
from core.claims import Claim
from core.news import fetch_news, rank_articles

MATERIAL_MOVE_PCT = 1.0  # a move smaller than this (in either window) is not worth a claim
MAX_ARTICLES_SHOWN = 6

SYSTEM_PROMPT = (
    "You are the Currency Analyst for Ganga AI Solutions, a UK maker of GPU AI servers that pays suppliers abroad. "
    "You are given exchange-rate numbers that were ALREADY computed from ECB data - do not recalculate or change them. "
    "Your job is only to explain, in plain words, what the move means for the cost of one part, and to pick which of "
    "the numbered news articles discuss the currency or its drivers. Use ONLY the articles given. "
    "Do NOT state which currency Ganga is invoiced in (you do not know it); say the effect applies IF the supplier's currency move passes through. "
    "Reply with a single JSON object and nothing else."
)

FORMAT = """Reply with exactly this JSON (claim: 1-2 sentences using the numbers above; reasoning: 1-2 sentences):
{"claim": "...", "reasoning": "...", "source_ids": ["A1", "A2"]}
Pick the articles (by id) that discuss this currency, the pound, or what is moving them. If none do, use []."""


class CurrencyAnalyst(AnalystBase):
    name = "currency"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.notes = {}  # work_id -> why there is no claim

    @staticmethod
    def _query(target, currency, broaden=False):
        name = fx.CURRENCY_NAME.get(currency, currency)
        return (f"{target.country} economy currency central bank {name} sterling" if broaden
                else f"{name} pound sterling exchange rate")

    def _news(self, target, work_id, currency, broaden=False):
        query = self._query(target, currency, broaden)
        articles, info = fetch_news(query, refresh=self.refresh_news, publisher_feeds=False)
        self._add_to_pool(articles)
        self.world.record_articles(articles, target.part_name, target.country, self.name)
        self.trace.event(self.name, "news:fetch", input_summary=query,
                         output_summary=f"{len(articles)} articles ({'cache' if info['from_cache'] else 'live'}, fetched {info['fetched_at']})",
                         sources=[a.url for a in articles[:5]])
        name = fx.CURRENCY_NAME.get(currency, currency).split()
        return rank_articles(articles, [(name, 3), (["pound", "sterling", "gbp", "exchange", "currency", "forex"], 2)],
                             k=MAX_ARTICLES_SHOWN)

    def _move(self, target, work_id):
        currency = fx.COUNTRY_CURRENCY.get(target.country)
        if currency is None:
            self.notes[work_id] = f"no currency mapped for {target.country}"
            return None, None
        try:
            move = fx.fetch_fx_move(currency)
        except fx.FXUnavailable as exc:
            self.notes[work_id] = f"{currency}: {exc}"
        except Exception as exc:  # network failure of the free API
            self.notes[work_id] = f"{currency}: FX API error ({type(exc).__name__})"
        else:
            return currency, move
        self.trace.event(self.name, "fx:unavailable", input_summary=f"{target.country} {currency}",
                         output_summary=self.notes[work_id], decision="NO_DATA")
        return currency, None

    def draft(self, target, work_id):
        currency, move = self._move(target, work_id)
        if move is None:
            return None
        m30, m90 = move.move_30d_pct, move.move_90d_pct
        if max(abs(m30), abs(m90)) < MATERIAL_MOVE_PCT or (m30 * m90 < 0):
            self.notes[work_id] = (f"GBP/{currency} moved {m30:+.2f}% (30d) and {m90:+.2f}% (90d): "
                                   f"{'mixed direction' if m30 * m90 < 0 else 'below the 1% materiality threshold'}")
            self.trace.event(self.name, "fx:immaterial", input_summary=f"{target.country} {currency}",
                             output_summary=self.notes[work_id], decision="NO_CLAIM")
            return None
        fx_article = fx.fx_source_article(move)
        self._add_to_pool([fx_article])
        self.world.record_articles([fx_article], target.part_name, target.country, self.name)
        shown = self._news(target, work_id, currency)
        return self._ask(target, work_id, currency, move, shown, attempt=1, previous=None, feedback=None)

    def revise(self, target, work_id):
        previous_key, feedback = self.feedback_for(work_id)
        previous = self.world.get_claim(previous_key)
        currency, move = self._move(target, work_id)
        if move is None:
            return None
        shown = self._news(target, work_id, currency, broaden=True)
        return self._ask(target, work_id, currency, move, shown, attempt=previous.attempt + 1, previous=previous, feedback=feedback)

    def _ask(self, target, work_id, currency, move, shown, attempt, previous, feedback):
        m30, m90 = move.move_30d_pct, move.move_90d_pct
        direction = "cost_up" if m90 > 0 else "cost_down"
        pct_low, pct_high = sorted((round(abs(m30), 2), round(abs(m90), 2)))
        numbers = (f"Computed from ECB reference rates (Frankfurter API): the cost of 1 {currency} in pounds changed "
                   f"{m30:+.2f}% over 30 days (to {move.end_date}) and {m90:+.2f}% over 90 days (from {move.date_90d}). "
                   f"1 GBP = {move.end_rate} {currency} now, {move.start_rate} on {move.date_90d}.\n"
                   f"Part: {target.part_id} {target.part_name}, bought from {target.country} (supplier currency {currency}); "
                   f"Ganga spent GBP {target.quarterly_spend_gbp:,.0f} on it in {target.quarter}. "
                   f"Assume the move passes through to Ganga's cost in full (an upper bound). Direction: "
                   f"{'cost UP' if direction == 'cost_up' else 'cost DOWN'}; estimated range {pct_low}-{pct_high}%.\n\n")
        listing = "\n".join(f"A{i} | {a.published[:10]} | {a.publisher} | {a.title}" for i, a in enumerate(shown, 1)) or "(no articles found)"
        task = numbers + f"News articles:\n{listing}\n\n"
        if previous is None:
            task += "Explain the effect on this part's cost.\n" + FORMAT
        else:
            task += (f"Your previous explanation was sent back by the Evaluator.\nPrevious claim: {previous.claim}\n"
                     f"Evaluator feedback: {' | '.join(feedback)}\nFix every point of feedback.\n" + FORMAT)

        data, result = self._ask_json(SYSTEM_PROMPT, task, "claim" if previous is None else "revise", attempt - 1)
        if not data or not data.get("claim"):
            self.trace.event(self.name, "claim:none", output_summary="analyst returned no explanation", model=result.model,
                             retry_count=attempt - 1)
            return None
        by_id = {f"A{i}": a for i, a in enumerate(shown, 1)}
        news_urls = [by_id[s].url if s in by_id else str(s) for s in data.get("source_ids", [])]
        claim = Claim(
            claim_id=work_id, claim=str(data["claim"]).strip(), part_id=target.part_id, part_name=target.part_name,
            supplier_country=target.country, direction=direction, pct_low=pct_low, pct_high=pct_high,
            source_urls=[move.url] + news_urls,  # the FX data source is always cited, with its date
            reasoning=str(data.get("reasoning", "")).strip(), attempt=attempt, analyst=self.name,
            computed={"currency": currency, "move_30d_pct": m30, "move_90d_pct": m90, "start_date": move.start_date,
                      "end_date": move.end_date, "start_rate": move.start_rate, "end_rate": move.end_rate,
                      "pass_through": "100% (upper bound)"},
        )
        self.world.write_claim(claim, work_id, self.name)
        self.trace.event(self.name, "claim:draft" if previous is None else "claim:revised",
                         input_summary=f"{target.part_id} / {target.country} / {currency}",
                         output_summary=f"{direction} {pct_low}-{pct_high}% (computed): {claim.claim}", model=result.model,
                         sources=claim.source_urls, retry_count=attempt - 1)
        return claim
