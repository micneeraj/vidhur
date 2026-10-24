"""Supplier & Commodity Analyst: memory-chip price moves and supplier concentration / disruption risk for one part.

It is the Geopolitics Analyst's pipeline (news fetch, trust-ranked evidence, claim parsing, % label from a cited article or the
scenario assumptions) with a different question: what are prices, capacity and the supplier doing, not what are governments doing.
Claims use the same Claim format, so the Evaluator, calculator, Decision Maker and Red Team treat them like any other driver.
"""

import json
import re

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
Cite at least two articles from different publishers whenever the articles allow it. source_ids may only contain ids that are
listed above (A1, A2, ...): never cite an id that is not in the list and never cite an article you were not shown. The driver must be a memory price or supply
development (HBM/DRAM contract or spot prices, supplier capacity or allocation, shortages, supplier concentration or disruption,
export rules that stop or limit the supplier shipping); a currency move belongs to another analyst, and general tariffs or sanctions
that do not name memory chips or the supplier's country belong to the geopolitics analyst - never base a claim on them. If the
articles show no such development affecting this part, reply {"claims": []}."""


def claims_from_reply(text):
    """The {"claims": [...]} object in a model reply, even with reasoning text, echoed examples or a broken wrapper around it.

    The last JSON object in a reply is not always the answer (a trailing '{"claims": []}' echoed from the instructions, or a
    nested object when the outer braces do not parse). Prefer the last object whose "claims" list is non-empty, then any
    "claims" object, then a bare claim object (wrapped), and raise ValueError only if none is found.
    """
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    decoder, found, pos = json.JSONDecoder(), [], cleaned.find("{")
    while pos != -1:
        try:
            obj, _ = decoder.raw_decode(cleaned[pos:])
            if isinstance(obj, dict):
                found.append(obj)
        except json.JSONDecodeError:
            pass
        pos = cleaned.find("{", pos + 1)  # nested objects too: a claim is still usable when its wrapper is broken
    wrapped = [o for o in found if isinstance(o.get("claims"), list)]
    for obj in reversed(wrapped):
        if obj["claims"]:
            return obj
    if wrapped:
        return wrapped[-1]
    for obj in reversed(found):
        if "claim" in obj and "source_ids" in obj:
            return {"claims": [obj]}
    raise ValueError("No claims object found in model reply")


class SupplierCommodityAnalyst(GeopoliticsAnalyst):
    name = "supplier_commodity"
    SYSTEM_PROMPT = SYSTEM_PROMPT
    CLAIM_FORMAT = CLAIM_FORMAT
    _parse_reply = staticmethod(claims_from_reply)

    @staticmethod
    def _query(target, broaden=False):
        if broaden:
            return f"{target.category} DRAM contract price {target.country} supplier"
        return f"{target.country} {target.category} price OR shortage OR capacity OR export"

    @staticmethod
    def _keywords(target):
        return [([target.country, "hbm", "dram", "memory"], 3), (target.category.split() + target.part_name.split()[1:3], 2),
                (["price", "contract", "capacity", "shortage", "supply", "allocation", "demand", "export", "wafer", "output"], 1)]
