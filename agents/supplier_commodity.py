"""Supplier & Commodity Analyst: memory-chip price moves and supplier concentration / disruption risk for one part.

It is the Geopolitics Analyst's pipeline (news fetch, trust-ranked evidence, claim parsing, % label from a cited article or the
scenario assumptions) with a different question: what are prices, capacity and the supplier doing, not what are governments doing.
Claims use the same Claim format, so the Evaluator, calculator, Decision Maker and Red Team treat them like any other driver.
"""

from agents.geopolitics_analyst import GeopoliticsAnalyst

SYSTEM_PROMPT = (
    "You are the Supplier & Commodity Analyst for Ganga AI Solutions, a UK maker of GPU AI servers. "
    "You assess how memory-chip market news (HBM and DRAM contract prices, supplier capacity, allocation and shortages, "
    "supplier concentration, supplier disruptions) could change the cost of ONE part bought from ONE supplier. "
    "Use ONLY the numbered articles you are given. Never invent facts, articles or ids. "
    "Reply with a single JSON object and nothing else."
)

CLAIM_FORMAT = """Reply with exactly this JSON (keep driver to a few words, claim to 1-2 sentences and reasoning to 1-2 sentences):
{"claims": [{"driver": "...", "claim": "...", "part_id": "<part id above>", "supplier_country": "<country above>",
  "direction": "cost_up" or "cost_down", "source_ids": ["A1", "A2"],
  "stated_pct": null or {"low": <number>, "high": <number>, "source_id": "A1"}, "reasoning": "..."}]}
Rules: you do NOT calculate any money. Never write a GBP amount. Never estimate a percentage yourself: set stated_pct only if a
cited article itself prints a percentage price change for this kind of memory (copy its numbers exactly and give its id), otherwise
null - Python then applies the scenario assumptions. Do not put any % or figure in claim or reasoning unless an article prints it.
Cite at least two articles from different publishers whenever the articles allow it. The driver must be a memory price or supply
development (HBM/DRAM contract or spot prices, supplier capacity or allocation, shortages, supplier concentration or disruption,
export rules that stop or limit the supplier shipping); a currency move belongs to another analyst, and general tariffs or sanctions
that do not name memory chips or the supplier's country belong to the geopolitics analyst - never base a claim on them. If the
articles show no such development affecting this part, reply {"claims": []}."""


class SupplierCommodityAnalyst(GeopoliticsAnalyst):
    name = "supplier_commodity"
    SYSTEM_PROMPT = SYSTEM_PROMPT
    CLAIM_FORMAT = CLAIM_FORMAT

    @staticmethod
    def _query(target, broaden=False):
        if broaden:
            return f"{target.category} DRAM contract price {target.country} supplier"
        return f"{target.country} {target.category} price OR shortage OR capacity OR export"

    @staticmethod
    def _keywords(target):
        return [([target.country, "hbm", "dram", "memory"], 3), (target.category.split() + target.part_name.split()[1:3], 2),
                (["price", "contract", "capacity", "shortage", "supply", "allocation", "demand", "export", "wafer", "output"], 1)]
