"""Decision Maker: turns a verified claim into a recommended decision, then defends or revises it against the Red Team.

The model only PICKS an option (kind + share) and explains it. Every GBP figure is computed in Python (core/decisions.py),
including after a revision - any number the model writes is ignored.

The challenge loop (non-linear on purpose):
  Red Team objects -> Decision Maker REVISES or DEFENDS each high/medium objection (round 1)
  -> Red Team re-judges (and may add a new objection if the revision created one) -> Decision Maker responds again (round 2)
  -> Red Team gives its closing judgement. Maximum 2 rounds. A high objection still standing after round 2 makes the decision
  "CONTESTED - needs CFO judgement" instead of a confident recommendation.
"""

from concurrent.futures import ThreadPoolExecutor

import openai
import requests

from core import config, decisions, llm
from core.red_team import BRIEF_REASONING, MAX_ATTEMPTS, RedTeam, _number_tokens, evidence_block, known_ids, lean_block, ref_valid
from core.source_trust import trust_of

MAX_ROUNDS = 2
MAX_DECISIONS = int(config.env("DECISION_MAX", "4"))
MAX_TOKENS = 4000
CONFIDENCE_RANK = {"Low": 0, "Medium": 1, "High": 2}

SYSTEM_PROMPT = (
    "You are the Decision Maker for Ganga AI Solutions, a UK maker of GPU AI servers. A verified cost-risk claim has been "
    "audited; you recommend what the CFO should do about it: no action, hedge a share of next quarter's spend, lock the price, "
    "or move volume to a second supplier. You choose the option and explain it using the option costs you are given. "
    "Never calculate GBP yourself: the figures are computed for you and recomputed after any change. "
    "Do not deliberate at length: answer directly, each reason under 30 words. "
    "Reply with a single JSON object and nothing else."
)

# --demo-red-team: appended to the FIRST proposal of one decision only. The model call, the Red Team and the loop are real.
DEMO_AGGRESSIVE_INSTRUCTION = (
    "DEMO MODE (deliberately aggressive decision, requested by the operator): propose kind HEDGE with share_pct 100 for this part, "
    "whatever the confidence or the evidence, and justify it as best you can."
)
PROPOSAL_FORMAT = ('{"kind": "NO_ACTION" or "HEDGE" or "LOCK_PRICE" or "DUAL_SOURCE", "share_pct": <5-100, the share of next '
                   'quarter\'s spend protected; ignored for NO_ACTION and LOCK_PRICE>, "rationale": "<1-3 sentences citing option costs, '
                   'confidence or source ids>"}')
RESPONSE_FORMAT = ('{"responses": [{"objection_id": "O1", "action": "REVISE" or "DEFEND", "reason": "<1-2 sentences>", '
                   '"evidence_refs": ["S1", "£12,345"]}], "revised_decision": {"kind": "...", "share_pct": <number>, "rationale": "..."}}')


# ---------------------------------------------------------------------------
# Pure helpers (unit tested without any model)
# ---------------------------------------------------------------------------
def make_decision(spend_gbp, impact, kind, share_pct, rationale, version, source, **extra):
    """A decision with its GBP cost under every scenario, ALWAYS computed here in Python."""
    cost = decisions.option_cost(spend_gbp, impact, kind, share_pct)
    return {"version": version, "kind": kind, "share_pct": share_pct, "rationale": rationale, "source": source,
            "premium_gbp": cost["premium_gbp"], "cost": {k: cost[k] for k in decisions.SCENARIOS}, **extra}


def parse_proposal(data):
    """(kind, share, rationale) from a model reply, or None if unusable."""
    if not isinstance(data, dict):
        return None
    choice = decisions.normalize_choice(data.get("kind"), data.get("share_pct"))
    rationale = str(data.get("rationale", "")).strip()
    return (*choice, rationale) if choice and rationale else None


def apply_responses(ev, decision, actionable, data):
    """Validate the Decision Maker's reply to the actionable objections and apply any revision.

    Returns (responses {objection_id: {action, reason, evidence_refs}}, new_decision or None).
    action is REVISE, DEFEND, or NONE when the reply for that objection is missing or invalid (DEFEND needs evidence
    references; REVISE needs a usable, DIFFERENT revised decision). The revised decision's GBP is recomputed here.
    """
    rows = decisions.compare_options(ev["spend_gbp"], ev["impact"], (decision["kind"], decision["share_pct"]))
    ids, numbers = known_ids(ev, rows), _number_tokens(evidence_block(ev, rows, decision))
    raw = {str(r.get("objection_id", "")).strip(): r for r in ((data or {}).get("responses") or []) if isinstance(r, dict)} \
        if isinstance(data, dict) else {}
    rd = (data or {}).get("revised_decision") if isinstance(data, dict) else None
    revised = None
    if isinstance(rd, dict):
        choice = decisions.normalize_choice(rd.get("kind"), rd.get("share_pct"))
        if choice and choice != (decision["kind"], decision["share_pct"]):
            revised = (*choice, str(rd.get("rationale", "")).strip() or decision["rationale"])

    responses = {}
    for o in actionable:
        r = raw.get(o["id"], {})
        action, reason = str(r.get("action", "")).strip().upper(), str(r.get("reason", "")).strip()
        refs = [x for x in (r.get("evidence_refs") or []) if ref_valid(x, ids, numbers)] if isinstance(r.get("evidence_refs"), list) else []
        if action == "REVISE" and reason and revised:
            responses[o["id"]] = {"action": "REVISE", "reason": reason, "evidence_refs": refs}
        elif action == "DEFEND" and reason and refs:
            responses[o["id"]] = {"action": "DEFEND", "reason": reason, "evidence_refs": refs}
        else:
            why = ("DEFEND without a specific evidence reference" if action == "DEFEND" else
                   "REVISE without a usable, different revised decision" if action == "REVISE" else "no valid response")
            responses[o["id"]] = {"action": "NONE", "reason": f"invalid: {why}", "evidence_refs": []}
    new = None
    if revised and any(v["action"] == "REVISE" for v in responses.values()):
        new = make_decision(ev["spend_gbp"], ev["impact"], revised[0], revised[1], revised[2], decision["version"] + 1, "revision")
    return responses, new


def handling(objection):
    """How an objection ended, for the CFO brief."""
    if objection["severity"] == "low":
        return "NOTED"
    if objection["status"] == "resolved":
        return {"REVISE": "REVISED", "DEFEND": "DEFENDED"}.get(objection.get("response", {}).get("action"), "RESOLVED")
    return "CONTESTED" if objection["severity"] == "high" else "OPEN"


# ---------------------------------------------------------------------------
# The Decision Maker agent
# ---------------------------------------------------------------------------
class DecisionMaker:
    name = "decision_maker"

    def __init__(self, trace, model=None):
        self.trace = trace
        self.model = model or config.analyst_model()
        self.expected_model = config.analyst_expected() if model is None else model

    def _ask(self, prompt, step):
        result = llm.chat([{"role": "system", "content": SYSTEM_PROMPT + BRIEF_REASONING},
                           {"role": "user", "content": prompt}],
                          model=self.model, expected_model=self.expected_model, agent=self.name, step=step,
                          trace=self.trace, max_tokens=MAX_TOKENS, max_attempts=MAX_ATTEMPTS)
        try:
            return llm.extract_json(result.text)
        except ValueError:
            return None

    def propose(self, ev, aggressive=False):
        rows = decisions.compare_options(ev["spend_gbp"], ev["impact"])
        prompt = (f"{evidence_block(ev, rows, None)}\n\nRecommend ONE option for next quarter. Options: {', '.join(decisions.KINDS)}. "
                  "Weigh the cost of protection against the downside it removes, the confidence in the forecast, and concentration. "
                  "Reply exactly:\n" + PROPOSAL_FORMAT + (("\n" + DEMO_AGGRESSIVE_INSTRUCTION) if aggressive else ""))
        parsed = parse_proposal(self._ask(prompt, "propose"))
        if parsed is None:
            self.trace.event(self.name, "propose:fallback", input_summary=ev["work_id"], decision="FALLBACK",
                             output_summary="model reply unusable; documented fallback: no action, monitor")
            return make_decision(ev["spend_gbp"], ev["impact"], "NO_ACTION", 0,
                                 "Fallback: the decision model's reply was unusable, so the default is to monitor.", 1, "fallback")
        kind, share, rationale = parsed
        return make_decision(ev["spend_gbp"], ev["impact"], kind, share, rationale, 1, "model", demo_aggressive=aggressive)

    def respond(self, ev, decision, actionable):
        rows = decisions.compare_options(ev["spend_gbp"], ev["impact"], (decision["kind"], decision["share_pct"]))
        listing = "\n".join(f"{o['id']} [{o['severity']}, {o['type']}]: {o['text']} (cites: {', '.join(o['evidence_refs'])}). "
                            f"Suggested: {o['suggested_change'] or 'none'}" for o in actionable)
        prompt = (f"{lean_block(ev, rows, decision)}\n\nThe Red Team raised these objections to your decision:\n{listing}\n\n"
                  "Respond to EVERY objection id: REVISE (change the decision) or DEFEND (keep it, with a reason that cites evidence ids "
                  "or numbers from above). If you revise, give one revised_decision that addresses the objections (for example a smaller "
                  "share, another option, or a second supplier); do not write GBP figures. Reply exactly:\n" + RESPONSE_FORMAT)
        return apply_responses(ev, decision, actionable, self._ask(prompt, "respond"))


# ---------------------------------------------------------------------------
# Evidence context and the challenge loop
# ---------------------------------------------------------------------------
def build_evidence(item, world, targets):
    """Everything the Decision Maker and Red Team may cite for one verified claim, read from the world state + Zetaris data."""
    from agents.evaluator import source_profile
    from core.news import Article

    claim, impact, d = item["attempts"][-1]["claim"], item["impact"], item["delegation"]
    sources, articles = [], []
    for i, url in enumerate(dict.fromkeys(claim.source_urls), 1):
        art = world.article(url)
        if not art:
            continue
        publisher, tier, _ = trust_of(url, art["publisher"])
        sources.append({"id": f"S{i}", "publisher": art["publisher"], "tier": tier, "date": art["published"][:10], "title": art["title"][:110]})
        articles.append(Article(art["title"], art["publisher"], url, art["published"]))
    last_verdict = item["attempts"][-1]["verdicts"][-1] if item["attempts"][-1]["verdicts"] else {}
    return {"work_id": d["work_id"], "analyst": claim.analyst, "part_id": claim.part_id, "part_name": claim.part_name,
            "claim": claim.claim, "direction": claim.direction, "pct_low": claim.pct_low, "pct_high": claim.pct_high,
            "confidence": impact["confidence"], "strength": last_verdict.get("strength", ""), "retries": d.get("retries", 0),
            "strength_line": source_profile(articles)["summary"] if articles else "no fetched sources",
            "spend_gbp": impact["baseline_quarterly_spend_gbp"], "impact": impact, "sources": sources,
            "concentration": decisions.concentration(targets, claim.part_id)}


def challenge_decision(ev, dm, rt, world, trace, aggressive=False, max_rounds=MAX_ROUNDS):
    """Propose -> Red Team objects -> (Decision Maker responds -> Red Team re-judges) up to max_rounds. Returns the outcome dict."""
    work_id, spend, impact = ev["work_id"], ev["spend_gbp"], ev["impact"]
    decision = dm.propose(ev, aggressive=aggressive)
    first = decision
    world.write_decision(work_id, decision)
    trace.event(dm.name, "propose", input_summary=work_id, decision=f"{decision['kind']} {decision['share_pct']}%",
                output_summary=decision["rationale"], cost=decision["cost"], demo_aggressive=bool(aggressive))

    def rows_for(dec):
        return decisions.compare_options(spend, impact, (dec["kind"], dec["share_pct"]))

    objections = []

    def add(raw_list, round_no):
        for raw in raw_list:
            o = {"id": f"O{len(objections) + 1}", **raw, "status": "open", "raised_in_round": round_no,
                 "decision_version": decision["version"], "response": {}}
            objections.append(o)
            world.write_objection(work_id, o, decision["version"])
            trace.event(rt.name, "objection", input_summary=f"{work_id} {o['id']}", decision=o["severity"].upper(),
                        output_summary=f"[{o['type']}] {o['text']}", objection_id=o["id"], objection_type=o["type"],
                        evidence_refs=o["evidence_refs"], round=round_no)

    add(rt.challenge(ev, rows_for(decision), decision), 0)
    rounds = 0
    if not objections:
        trace.event(rt.name, "objections:none", input_summary=work_id, decision="CLEAR" if rt.last_clear else "UNCHALLENGED",
                    output_summary="no line of attack applies to this decision (checked in Python)" if rt.last_clear
                    else "no objection with a specific evidence reference survived validation")
    while rounds < max_rounds:
        actionable = [o for o in objections if o["status"] == "open" and o["severity"] in ("high", "medium")]
        if not actionable:
            break
        rounds += 1
        responses, revised = dm.respond(ev, decision, actionable)
        for o in actionable:
            o["response"] = responses[o["id"]]
            trace.event(dm.name, "response", input_summary=f"{work_id} {o['id']}", decision=o["response"]["action"],
                        output_summary=o["response"]["reason"], objection_id=o["id"], round=rounds)
        if revised:
            before = decision
            decision = revised
            world.write_decision(work_id, decision)
            trace.event(dm.name, "revised", input_summary=work_id, decision=f"{decision['kind']} {decision['share_pct']}%",
                        output_summary=f"{before['kind']} {before['share_pct']}% (likely {decisions.gbp(before['cost']['likely'])}, "
                                       f"if wrong {decisions.gbp(before['cost']['forecast_wrong'])}) -> {decision['kind']} "
                                       f"{decision['share_pct']}% (likely {decisions.gbp(decision['cost']['likely'])}, "
                                       f"if wrong {decisions.gbp(decision['cost']['forecast_wrong'])})", round=rounds, cost=decision["cost"])
        final = rounds == max_rounds
        statuses, new = rt.follow_up(ev, rows_for(decision), decision, actionable, responses, final=final)
        for o in actionable:
            s = statuses[o["id"]]
            o["status"] = "resolved" if s["status"] == "resolved" else "standing"
            o["red_team_review"] = s["reason"]
            trace.event(rt.name, "review", input_summary=f"{work_id} {o['id']}", decision=o["status"].upper(),
                        output_summary=s["reason"], objection_id=o["id"], round=rounds)
        add(new, rounds)
        for o in objections:
            o["handling"] = handling(o)
            world.write_objection(work_id, o, o["decision_version"])
        trace.event(rt.name, "round", input_summary=work_id, round=rounds,
                    decision="OPEN" if any(o["status"] in ("open", "standing") and o["severity"] != "low" for o in objections) else "CLEARED",
                    output_summary=" | ".join(f"{o['id']}:{o['handling']}" for o in objections))
        for o in objections:  # a standing objection is re-opened for the next round (the loop's max_rounds still bounds it)
            if o["status"] == "standing" and rounds < max_rounds:
                o["status"] = "open"

    for o in objections:
        if o["status"] == "open":
            o["status"] = "standing"  # still unresolved when the rounds ran out
        o["handling"] = handling(o)
        world.write_objection(work_id, o, o["decision_version"])
    contested = any(o["severity"] == "high" and o["status"] == "standing" for o in objections)
    status = "CONTESTED" if contested else ("UNCHALLENGED" if not objections and not rt.last_clear else "RECOMMENDED")
    outcome = {"status": status, "rounds": rounds, "objection_count": len(objections),
               "changed": (first["kind"], first["share_pct"]) != (decision["kind"], decision["share_pct"]),
               "first_version": first["version"], "final_version": decision["version"]}
    world.write_decision_outcome(work_id, outcome)
    trace.event(dm.name, "outcome", input_summary=work_id, decision=status,
                output_summary=f"{rounds} round(s); {len(objections)} objection(s); final {decision['kind']} {decision['share_pct']}%"
                               + ("; needs CFO judgement" if contested else ""), retry_count=rounds)
    return outcome


def run_decisions(report, world, targets, trace, demo_red_team=False, workers=2):
    """Decide on every claim that passed the Evaluator (largest baseline first, capped), challenge each, write all to the world."""
    passed = [i for i in report["items"] if i["delegation"].get("action") == "run" and i["delegation"].get("status") == "PASS" and i["impact"]]
    passed.sort(key=lambda i: -i["impact"]["baseline_quarterly_spend_gbp"])
    passed = passed[:MAX_DECISIONS]
    if not passed:
        return {}
    weakest = min(passed, key=lambda i: CONFIDENCE_RANK.get(i["impact"]["confidence"], 1))["delegation"]["work_id"] if demo_red_team else None
    dm, rt = DecisionMaker(trace), RedTeam(trace)

    def one(item):
        work_id = item["delegation"]["work_id"]
        with trace.target(work_id):
            try:
                ev = build_evidence(item, world, targets)
                return challenge_decision(ev, dm, rt, world, trace, aggressive=(work_id == weakest))
            except (openai.OpenAIError, requests.RequestException) as exc:
                message = f"{type(exc).__name__}: {str(exc)[:200]}"
                # A model outage must not pass for a challenged decision: a proposal that was never challenged is labelled as such.
                has_proposal = bool(world.decision_report().get(work_id, {}).get("versions"))
                status = "NOT_CHALLENGED" if has_proposal else "NOT_DECIDED"
                world.write_decision_outcome(work_id, {"status": status, "error": message, "rounds": 0})
                trace.event(dm.name, "error", input_summary=work_id, output_summary=message, decision=status)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(one, passed))
