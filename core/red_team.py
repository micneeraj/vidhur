"""Red Team: the Evaluator checks the CLAIMS, the Red Team checks the DECISION - like a sceptical CFO or risk committee.

For each proposed decision it returns structured objections (JSON). Every objection must name a type, a severity and
specific evidence: an evidence id (S1.. for sources, the work id, an option kind) or a number that appears in the
evidence. An objection without that is dropped; if none survive, the model is asked once more.
Objections and their outcomes are written to the World State by the caller (agents/decision_maker.py).
"""

import re

from core import config, decisions, llm

SEVERITIES = ("high", "medium", "low")
TYPES = ("forecast_wrong", "cost_of_acting", "concentration", "weak_evidence", "cheaper_alternative")
STATUSES = ("resolved", "stands")
MAX_OBJECTIONS = 3
MAX_TOKENS = 4000
CALL_TIMEOUT_SECONDS = 300  # the Red Team reasons for 2-4 minutes on the live runtime; overrides the demo run-wide 180 s cap
MAX_ATTEMPTS = int(config.env("RED_TEAM_LLM_ATTEMPTS", "8"))  # these calls sit near the runtime deadline, so more tries per call
# Measured on the live runtime: with this hint 3 of 4 proposal calls were answered; with "/no_think" or no hint, 0 of 4.
BRIEF_REASONING = " Keep your private reasoning brief."
CHECK_TYPES = {1: "forecast_wrong", 2: "cost_of_acting", 3: "concentration", 4: "weak_evidence", 5: "cheaper_alternative"}

SYSTEM_PROMPT = (
    "You are a sceptical CFO on a risk committee reviewing a procurement decision. You attack the DECISION, not the news claim "
    "(another agent verified that). Cite S-ids or numbers copied from the text; never invent facts. JSON only."
)

OBJECTION_FORMAT = (
    '{"objections":[{"check":<1-5, the number of the check>,"severity":"high" or "medium" or "low","objection":"<max 25 words>",'
    '"refs":["<an S-id such as S1>","<a number copied from the text above>"],"fix":"<max 15 words>"}]}'
)


# ---------------------------------------------------------------------------
# Pure helpers (unit tested without any model)
# ---------------------------------------------------------------------------
def _number_tokens(text):
    """Numbers in a text, normalised (no commas, no trailing .0). Single digits are ignored: too easy to match by luck."""
    out = set()
    for token in re.findall(r"\d[\d,]*\.?\d*", text or ""):
        token = token.replace(",", "").rstrip(".")
        if "." in token:
            token = token.rstrip("0").rstrip(".")
        if len(token.replace(".", "")) >= 2 or "." in token:
            out.add(token)
    return out


def known_ids(ev, rows):
    """Evidence ids an objection may cite: source ids, the work id, the option names, CLAIM."""
    ids = {s["id"].upper() for s in ev["sources"]}
    ids |= {ev["work_id"].upper(), "CLAIM", "CONFIDENCE", "CALC"} | {k for k in decisions.KINDS}
    return ids


def calc_line(ev):
    """One line describing how the GBP figures were calculated (id CALC), from the calculator's record; empty if there is none."""
    calc = (ev.get("impact") or {}).get("calc")
    if not calc:
        return ""
    m, fx = calc["move_pct"], calc["fx"]
    return (f"CALC (calculated in Python, never by a model): baseline GBP {calc['baseline_gbp']:,.0f} = Zetaris purchase rows at 1 GBP = "
            f"{fx['units_per_gbp']} {fx['currency']} on {fx['date']}; move {m['small']:g}% / {m['likely']:g}% / {m['large']:g}% ({m['label']}, "
            f"{m['basis']}); pass-through {calc['pass_through']['pct']:g}%. The worst / likely / best figures are a scenario range, not a probability.\n")


def evidence_block(ev, rows, decision):
    """The text the Red Team and the Decision Maker both see. Numbers in it are what 'specific numbers' means."""
    c = ev["concentration"]
    sources = "\n".join(f"{s['id']} | {s['publisher']} | Tier {s['tier']} | {s['date']} | {s['title']}" for s in ev["sources"])
    return (
        f"Decision {ev['work_id']} for {ev['part_id']} {ev['part_name']} (supplier {c['supplier']}, {c['country']}).\n"
        f"Verified claim ({ev['analyst']}): {ev['claim']}\n"
        f"Estimate: cost {ev['direction'].replace('cost_', '')} {ev['pct_low']:g}-{ev['pct_high']:g}%. Confidence: {ev['confidence']}. "
        f"Evidence strength: {ev['strength_line']}. Evaluator rated support: {ev['strength'] or 'n/a'}.\n"
        f"Baseline quarterly spend on this part: GBP {ev['spend_gbp']:,.0f}.\n"
        f"{calc_line(ev)}"
        f"Sources (ids to cite):\n{sources}\n"
        f"Concentration: this part is {c['part_share_pct']}% of Ganga's GBP {c['total_spend_gbp']:,} quarterly spend; {c['country']} is "
        f"{c['country_share_pct']}% and supplier {c['supplier']} is {c['supplier_share_pct']}%.\n"
        f"Option costs (change in quarterly cost vs baseline, computed in Python; assumed premiums: hedge "
        f"{decisions.PREMIUM_PCT['HEDGE']}%, price lock {decisions.PREMIUM_PCT['LOCK_PRICE']}%, second supplier "
        f"{decisions.PREMIUM_PCT['DUAL_SOURCE']}% of the protected spend):\n{decisions.options_table_text(rows)}\n"
    ) + ("" if decision is None else
         f"PROPOSED DECISION (version {decision['version']}): {decisions.describe(decision['kind'], decision['share_pct'])}. "
         f"Its cost: worst {decisions.gbp(decision['cost']['worst'])}, likely {decisions.gbp(decision['cost']['likely'])}, "
         f"best {decisions.gbp(decision['cost']['best'])}, if forecast wrong {decisions.gbp(decision['cost']['forecast_wrong'])}. "
         f"Rationale: {decision['rationale']}")


def lean_block(ev, rows, decision):
    """A short view of the decision for the Red Team and the response prompts (the full evidence_block stays the source of valid refs)."""
    sources = "; ".join(f"{s['id']} {s['publisher']} Tier {s['tier']} {s['date']}" for s in ev["sources"])
    return (f"Decision {ev['work_id']} on {ev['part_name']} ({ev['concentration']['country']}), version {decision['version']}: "
            f"{decisions.describe(decision['kind'], decision['share_pct'])}. Rationale: {decision['rationale']}\n"
            f"Claim: {ev['claim']} Confidence {ev['confidence']}; evidence: {ev['strength_line']}; baseline spend GBP {ev['spend_gbp']:,.0f}.\n"
            f"Sources: {sources}\n"
            f"Option costs (worst | likely | best | if forecast wrong):\n{decisions.options_table_text(rows)}")


def red_team_block(ev, decision):
    """What the Red Team needs and no more: the decision and its reasons, its GBP scenario range, the claim and an evidence summary.
    No option table (the computed checks already carry the comparisons) and no article text, to keep the call inside the runtime deadline."""
    sources = "; ".join(f"{s['id']} {s['publisher']} Tier {s['tier']} {s['date']}" for s in ev["sources"])
    cost = decision["cost"]
    return (f"Decision {ev['work_id']} on {ev['part_name']} ({ev['concentration']['country']}), version {decision['version']}: "
            f"{decisions.describe(decision['kind'], decision['share_pct'])}. Rationale: {decision['rationale']}\n"
            f"Its cost: worst {decisions.gbp(cost['worst'])}, likely {decisions.gbp(cost['likely'])}, best {decisions.gbp(cost['best'])}, "
            f"if forecast wrong {decisions.gbp(cost['forecast_wrong'])}. Baseline spend GBP {ev['spend_gbp']:,.0f}.\n"
            f"Claim: {ev['claim']} Confidence {ev['confidence']}; evidence: {ev['strength_line']}. Sources: {sources}")


COUNTRY_CONCENTRATION_PCT, SUPPLIER_CONCENTRATION_PCT = 30.0, 20.0  # shares of total spend that make concentration worth raising


def checklist(ev, rows, decision):
    """The lines of attack that APPLY to this decision, as facts computed in Python (one line per triggered check).

    The model then judges severity and wording for these only, instead of searching for angles - that keeps its reasoning short
    enough for the runtime deadline. Returns an empty string when nothing applies.
    """
    me = next(r for r in rows if (r["kind"], r["share_pct"]) == (decision["kind"], decision["share_pct"]))
    none = next(r for r in rows if r["kind"] == "NO_ACTION")
    exposure = lambda r: max(r["worst"], r["forecast_wrong"])  # noqa: E731
    others = [r for r in rows if r is not me]
    cheaper = [r for r in others if r["likely"] < me["likely"] or exposure(r) < exposure(me)]
    weakest = max(ev["sources"], key=lambda s: s["tier"], default=None)
    c = ev["concentration"]
    lines = []
    if me["share_pct"] > 0 and me["forecast_wrong"] > none["forecast_wrong"]:
        lines.append(f"1. FORECAST WRONG: if the market moves the other way by the likely amount, this decision costs "
                     f"{decisions.gbp(me['forecast_wrong'])} versus {decisions.gbp(none['forecast_wrong'])} with no action.")
    if me["premium_gbp"] > 0:
        lines.append(f"2. ACTING VS NOT ACTING: protection costs a {decisions.gbp(me['premium_gbp'])} premium; likely cost "
                     f"{decisions.gbp(me['likely'])} versus {decisions.gbp(none['likely'])} with no action.")
    if c["country_share_pct"] >= COUNTRY_CONCENTRATION_PCT or c["supplier_share_pct"] >= SUPPLIER_CONCENTRATION_PCT:
        lines.append(f"3. CONCENTRATION: {c['country']} is {c['country_share_pct']}% and {c['supplier']} {c['supplier_share_pct']}% of Ganga's "
                     f"quarterly spend; the decision protects {decision['share_pct']}% of this part.")
    if ev["confidence"] != "High" or ev["retries"] or (weakest and weakest["tier"] == 3):
        lines.append(f"4. WEAKEST EVIDENCE: confidence {ev['confidence']}, claim passed after {ev['retries']} send-back(s)"
                     + (f", weakest source {weakest['id']} ({weakest['publisher']}, Tier {weakest['tier']})." if weakest else "."))
    if cheaper:
        best = min(cheaper, key=lambda r: (r["likely"], exposure(r)))
        lines.append(f"5. CHEAPER ALTERNATIVE: {decisions.describe(best['kind'], best['share_pct'])} has likely cost "
                     f"{decisions.gbp(best['likely'])} and worst exposure {decisions.gbp(exposure(best))}, versus "
                     f"{decisions.gbp(me['likely'])} and {decisions.gbp(exposure(me))} for this decision.")
    return "\n".join(lines)


def ref_valid(ref, ids, numbers):
    ref = str(ref).strip()
    if not ref:
        return False
    if ref.upper() in ids or ref.upper().replace(" ", "_") in ids:
        return True
    return bool(_number_tokens(ref) & numbers)


def parse_objections(data, ids, numbers):
    """Validate a model reply. Returns (valid objections, rejected [(reason, raw)]).

    Valid = known type, known severity, non-empty text, and at least one evidence reference that is a known id or
    a number present in the evidence. Invalid refs are dropped from a kept objection.
    """
    valid, rejected = [], []
    raw_list = (data or {}).get("objections") if isinstance(data, dict) else None
    if not isinstance(raw_list, list):
        return [], [("no objections list", data)]
    for raw in raw_list[:MAX_OBJECTIONS]:
        if not isinstance(raw, dict):
            rejected.append(("not an object", raw))
            continue
        otype, severity = str(raw.get("type", "")).strip().lower(), str(raw.get("severity", "")).strip().lower()
        if not otype and str(raw.get("check", "")).strip().isdigit():
            otype = CHECK_TYPES.get(int(str(raw["check"]).strip()), "")
        text = str(raw.get("objection") or raw.get("text") or "").strip()
        refs = next((raw[k] for k in ("evidence_refs", "refs") if isinstance(raw.get(k), list)), [])
        good = [str(r).strip() for r in refs if ref_valid(r, ids, numbers)]
        if otype not in TYPES:
            rejected.append((f"unknown type {otype!r}", raw))
        elif severity not in SEVERITIES:
            rejected.append((f"unknown severity {severity!r}", raw))
        elif not text:
            rejected.append(("empty objection", raw))
        elif not good:
            rejected.append(("no specific evidence reference (id or number)", raw))
        else:
            valid.append({"type": otype, "severity": severity, "text": text, "evidence_refs": good,
                          "suggested_change": str(raw.get("suggested_change") or raw.get("fix") or "").strip()})
    return valid, rejected


def parse_statuses(data, objection_ids):
    """{objection_id: {"status", "reason"}} for the objections the Red Team re-judged. Anything not judged 'resolved' stands."""
    out = {}
    for raw in (data or {}).get("statuses", []) if isinstance(data, dict) else []:
        if not isinstance(raw, dict):
            continue
        oid, status = str(raw.get("objection_id", "")).strip(), str(raw.get("status", "")).strip().lower()
        if oid in objection_ids and status in STATUSES and str(raw.get("reason", "")).strip():
            out[oid] = {"status": status, "reason": str(raw["reason"]).strip()}
    return {oid: out.get(oid, {"status": "stands", "reason": "no valid re-assessment returned; treated as still standing"})
            for oid in objection_ids}


# ---------------------------------------------------------------------------
# The Red Team agent
# ---------------------------------------------------------------------------
class RedTeam:
    name = "red_team"

    def __init__(self, trace, model=None):
        self.trace = trace
        self.model = model or config.analyst_model()
        self.expected_model = config.analyst_expected() if model is None else model
        self.last_clear = False  # True when no line of attack applied to the last decision, so the model was not asked

    def _ask(self, prompt, step, retry_count=0):
        result = llm.chat([{"role": "system", "content": SYSTEM_PROMPT + BRIEF_REASONING},
                           {"role": "user", "content": prompt}],
                          model=self.model, expected_model=self.expected_model, agent=self.name, step=step,
                          trace=self.trace, retry_count=retry_count, max_tokens=MAX_TOKENS, max_attempts=MAX_ATTEMPTS, think=True, timeout=CALL_TIMEOUT_SECONDS)
        try:
            return llm.extract_json(result.text)
        except ValueError:
            return None

    def challenge(self, ev, rows, decision):
        """First pass: structured objections to a decision. Re-asks ONCE if none carries a specific reference."""
        block = evidence_block(ev, rows, decision)
        ids, numbers = known_ids(ev, rows), _number_tokens(block)
        checks = checklist(ev, rows, decision)
        self.last_clear = not checks
        if not checks:
            return []
        prompt = (f"{red_team_block(ev, decision)}\n\nChecks that apply, computed for you:\n{checks}\n\nFor each check say whether it is a "
                  f"real problem (at most {MAX_OBJECTIONS} objections, most serious first); severity high only if the decision should not "
                  "go ahead as proposed. Cite the numbers or ids above. Reply exactly:\n" + OBJECTION_FORMAT)
        valid, rejected = parse_objections(self._ask(prompt, "challenge"), ids, numbers)
        if not valid:
            self.trace.event(self.name, "objections:rejected", input_summary=ev["work_id"], decision="REASK",
                             output_summary="; ".join(r for r, _ in rejected)[:300] or "no usable objections")
            retry = prompt + ("\n\nYour previous reply had no objection with a specific evidence reference. Every objection MUST include "
                              "refs holding at least one of: " + ", ".join(sorted(ids)) + ", or a number that appears above.")
            valid, rejected = parse_objections(self._ask(retry, "challenge", retry_count=1), ids, numbers)
        return valid

    def follow_up(self, ev, rows, decision, objections, responses, final=False):
        """Review the Decision Maker's response. Returns ({objection_id: {status, reason}}, new_objections)."""
        block = evidence_block(ev, rows, decision)
        ids, numbers = known_ids(ev, rows), _number_tokens(block)
        listing = "\n".join(
            f"{o['id']} [{o['severity']}, {o['type']}]: {o['text']}\n    Response: {responses.get(o['id'], {}).get('action', 'NONE')} - "
            f"{responses.get(o['id'], {}).get('reason', 'no response')}" for o in objections)
        prompt = (f"{red_team_block(ev, decision)}\n\nObjections you raised and the Decision Maker's responses (the decision above is the CURRENT version, "
                  f"after any revision):\n{listing}\n\nFor each objection id say whether it is now resolved or still stands, with a "
                  "reason (max 25 words) that cites evidence.")
        fmt = ('{"statuses": [{"objection_id": "O1", "status": "resolved" or "stands", "reason": "<one sentence citing evidence>"}]'
               + ("" if final else ', "new_objections": [<same objection format as before, only if the revision created a NEW serious problem, same shape as before>]')
               + "}")
        data = self._ask(prompt + "\nReply exactly:\n" + fmt, "final-review" if final else "review")
        statuses = parse_statuses(data, [o["id"] for o in objections])
        new = [] if final else parse_objections({"objections": (data or {}).get("new_objections", [])}, ids, numbers)[0]
        return statuses, new
