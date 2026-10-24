"""Planner (Vidhur): decides WHICH analysts run for WHICH parts, and why - then reviews the outcome.

It reads Ganga's spend by part and supplier country from Zetaris plus a quick headline scan, asks the
model for a structured plan with a reason per delegation, and validates that plan in Python (unknown
parts or analysts are dropped, the number of delegations is capped). After the Evaluator stage it reviews
the world state and may re-delegate ONCE (e.g. ask the other analyst about a high-spend part that ended
UNVERIFIED or was not assessed).
"""

from concurrent.futures import ThreadPoolExecutor

from core import config, fx, llm
from core.news import fetch_news

ANALYSTS = {
    "geopolitics": "reads recent news on export controls, sanctions, tariffs and tensions affecting one part from one supplier country; "
                   "estimates a % cost change. Slow (several model calls).",
    "currency": "computes the real 30/90-day move of the supplier country's currency against the pound from ECB data and explains it; "
                "fast and numeric, but has NO data for some currencies (e.g. Taiwan dollar).",
    "supplier_commodity": "reads recent news on commodity/component price moves (e.g. HBM and DRAM contract prices), supplier capacity, "
                          "concentration and disruption for one part; estimates a % cost change. Best for memory and other commodity-driven parts. "
                          "Slow (several model calls).",
}
MAX_DELEGATIONS = int(config.env("PLANNER_MAX_DELEGATIONS", "4"))
MAX_TOKENS = 4000

SYSTEM_PROMPT = (
    "You are Vidhur, the Planner of a cost-risk team for Ganga AI Solutions, a UK maker of GPU AI servers. "
    "You do NOT analyse risk yourself: you decide which specialist analysts to run on which parts, and why, "
    "so the CFO's limited time and the team's limited runtime go where the money is. "
    "Reply with a single JSON object and nothing else."
)


def _scan_headlines(targets, per_country=3):
    """Quick headline scan: the newest Google News headlines for the largest-spend countries (cached)."""
    seen, picks = set(), []
    for t in targets:
        if t.country not in seen:
            seen.add(t.country)
            picks.append(t)
        if len(picks) == 3:
            break

    def scan(t):
        articles, _ = fetch_news(f"{t.country} {t.category} supply news", publisher_feeds=False)
        return t.country, [f"{a.title} ({a.publisher}, {a.published[:10]})" for a in articles[:per_country]]

    with ThreadPoolExecutor(3) as pool:
        return dict(pool.map(scan, picks))


class Planner:
    name = "planner"

    def __init__(self, trace, world):
        self.trace = trace
        self.world = world
        self.model = config.evaluator_model()  # the stronger lane: planning is the highest-leverage decision
        self.expected_model = config.evaluator_expected()

    def _ask(self, prompt, step):
        result = llm.chat([{"role": "system", "content": SYSTEM_PROMPT + " Keep your private reasoning brief."},
                           {"role": "user", "content": prompt}],
                          model=self.model, expected_model=self.expected_model, agent=self.name, step=step,
                          trace=self.trace, max_tokens=MAX_TOKENS)
        try:
            return llm.extract_json(result.text), result
        except ValueError:
            return None, result

    # ------------------------------------------------------------------ plan
    def plan(self, targets):
        """targets: every Ganga part with its latest-quarter spend, largest first (from Zetaris)."""
        total = sum(t.quarterly_spend_gbp for t in targets)
        headlines = _scan_headlines(targets)
        self.trace.event(self.name, "headline-scan", output_summary="; ".join(f"{c}: {len(h)} headlines" for c, h in headlines.items()))
        try:
            supported = fx.supported_currencies()
        except Exception:
            supported = set()

        rows = "\n".join(
            f"{t.part_id} | {t.part_name} | {t.country} | GBP {t.quarterly_spend_gbp:,.0f} ({t.quarterly_spend_gbp / total:.0%} of spend) | "
            f"priced in {t.fx_currency or '?'} "
            f"{'(FX data available)' if t.fx_currency in supported else '(NO FX data from the ECB)'}"
            for t in targets)
        news = "\n".join(f"{c}: " + " || ".join(h) if h else f"{c}: (no recent headlines)" for c, h in headlines.items())
        analysts = "\n".join(f"- {k}: {v}" for k, v in ANALYSTS.items())
        prompt = (
            f"Ganga's latest-quarter spend by part (from Zetaris):\n{rows}\n\nHeadline scan (largest-spend countries):\n{news}\n\n"
            f"Available analysts:\n{analysts}\n\n"
            f"Decide at most {MAX_DELEGATIONS} delegations in total. Put effort where spend is large or the headlines show a real "
            "risk; skip low-spend parts and analyst/part pairs that cannot work (say why). Every reason must cite spend, a headline, "
            "or data coverage. Reply exactly:\n"
            '{"summary": "<one sentence>", "delegations": [{"analyst": "geopolitics" or "currency" or "supplier_commodity", "part_id": "P00X", "reason": "..."}], '
            '"skipped": [{"analyst": "...", "part_id": "...", "reason": "..."}]}')
        data, result = self._ask(prompt, "plan")
        plan = self._validate(data, targets)
        if plan is None:
            # Not a mock: the model reply was unusable, so fall back to a simple documented rule, flagged everywhere.
            plan = self._fallback(targets)
            self.trace.event(self.name, "plan:fallback", output_summary="model plan unusable; using top-spend geopolitics rule",
                             decision="FALLBACK", model=result.model)
        plan["headlines"] = headlines
        self.world.record_plan(plan, [{"part_id": t.part_id, "country": t.country, "spend": t.quarterly_spend_gbp} for t in targets])
        for d in plan["delegations"]:
            self.trace.event(self.name, "delegate", input_summary=f"{d['analyst']} -> {d['part_id']}", output_summary=d["reason"],
                             decision="RUN", model=result.model)
        for s in plan["skipped"]:
            self.trace.event(self.name, "skip", input_summary=f"{s.get('analyst')} -> {s.get('part_id')}", output_summary=s.get("reason", ""),
                             decision="SKIP", model=result.model)
        return plan

    @staticmethod
    def _validate(data, targets):
        if not data or not isinstance(data.get("delegations"), list):
            return None
        valid_parts = {t.part_id for t in targets}
        seen, delegations = set(), []
        for d in data["delegations"]:
            analyst, part = str(d.get("analyst", "")).lower(), str(d.get("part_id", ""))
            if analyst not in ANALYSTS or part not in valid_parts or (analyst, part) in seen:
                continue
            seen.add((analyst, part))
            delegations.append({"analyst": analyst, "part_id": part, "reason": str(d.get("reason", "")).strip()})
        delegations = delegations[:MAX_DELEGATIONS]
        if not delegations:
            return None
        for i, d in enumerate(delegations, 1):
            d["work_id"] = f"D{i}"
        skipped = [{"analyst": str(s.get("analyst", "")), "part_id": str(s.get("part_id", "")), "reason": str(s.get("reason", "")).strip()}
                   for s in data.get("skipped", []) if isinstance(s, dict)]
        return {"summary": str(data.get("summary", "")).strip(), "delegations": delegations, "skipped": skipped, "fallback": False}

    @staticmethod
    def _fallback(targets):
        top = targets[:3]
        return {"summary": "FALLBACK PLAN (the planning model's reply was unusable): geopolitics on the top-3 spend parts.",
                "delegations": [{"analyst": "geopolitics", "part_id": t.part_id, "work_id": f"D{i}",
                                 "reason": f"fallback rule: top-{i} spend part (GBP {t.quarterly_spend_gbp:,.0f})"} for i, t in enumerate(top, 1)],
                "skipped": [], "fallback": True}

    # ------------------------------------------------------------------ review
    def review(self, report, targets):
        """Look at the world state after the Evaluator stage; return a new delegation dict or None. At most one."""
        spend = {t.part_id: t for t in targets}
        tried = {}
        passed = set()
        for item in report["items"]:
            d = item["delegation"]
            if d.get("action") != "run":
                continue
            tried.setdefault(d["part_id"], set()).add(d["analyst"])
            if d.get("status") == "PASS":
                passed.add(d["part_id"])
        candidates = []
        for part_id, analysts in tried.items():
            untried = [a for a in ANALYSTS if a not in analysts]
            if part_id not in passed and untried:
                candidates.append((spend[part_id].quarterly_spend_gbp, part_id, untried))
        if not candidates:
            review = {"redelegate": False, "reason": "No re-delegation needed or possible: every delegated part either has a verified "
                      "claim or has already been tried with every analyst.", "model_used": False}
            self.world.record_review(review)
            self.trace.event(self.name, "review", output_summary=review["reason"], decision="NO_REDELEGATION")
            return None

        _, part_id, untried = max(candidates)
        t = spend[part_id]
        outcomes = "; ".join(f"{i['delegation']['analyst']}->{i['delegation']['part_id']}: {i['delegation'].get('status')}"
                             + (f" ({i['delegation'].get('note') or i['delegation'].get('error') or ''})" if i['delegation'].get('status') != 'PASS' else '')
                             for i in report["items"] if i["delegation"].get("action") == "run")
        prompt = (f"Review of this run. Outcomes so far: {outcomes}.\n"
                  f"The highest-spend part with no verified claim is {t.part_id} {t.part_name} ({t.country}, GBP {t.quarterly_spend_gbp:,.0f} "
                  f"in {t.quarter}). Analysts not yet tried on it: {', '.join(untried)}. You may re-delegate ONCE for the whole run. "
                  'Is another attempt worth the runtime? Reply exactly: {"redelegate": true or false, "analyst": "<one of the untried>", "reason": "..."}')
        data, result = self._ask(prompt, "review")
        choice = str((data or {}).get("analyst", "")).lower()
        if data and data.get("redelegate") and choice in untried:
            work_id = f"D{len(report['items']) + 1}"
            delegation = {"analyst": choice, "part_id": part_id, "work_id": work_id, "reason": str(data.get("reason", "")).strip(),
                          "redelegation": True}
            self.world.record_review({"redelegate": True, "part_id": part_id, "analyst": choice, "reason": delegation["reason"], "work_id": work_id})
            self.world.add_delegation(delegation)
            self.trace.event(self.name, "review", input_summary=f"{part_id} had no verified claim", output_summary=delegation["reason"],
                             decision=f"REDELEGATE {choice}", model=result.model)
            return delegation
        reason = str((data or {}).get("reason", "")).strip() or "the planner's reply was unusable, so no re-delegation"
        self.world.record_review({"redelegate": False, "part_id": part_id, "reason": reason})
        self.trace.event(self.name, "review", input_summary=f"{part_id} had no verified claim", output_summary=reason,
                         decision="NO_REDELEGATION", model=result.model)
        return None
