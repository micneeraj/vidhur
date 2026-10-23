"""The CFO brief. Built ONLY from the world-state report (RunWorld.report()), never from local variables."""

from agents.decision_maker import DEMO_AGGRESSIVE_INSTRUCTION, MAX_ROUNDS, handling
from agents.evaluator import source_profile
from core import decisions as dec
from agents.geopolitics_analyst import DEMO_UNTRUSTED_INSTRUCTION, DEMO_WEAK_CLAIM_INSTRUCTION
from core.news import Article
from core.source_trust import trust_of


def gbp(value):
    return f"{'+' if value > 0 else '-' if value < 0 else ''}£{abs(value):,.0f}"


def _outcome(item):
    return item["delegation"].get("status", "?")


def _sources(world, claim):
    lines = []
    for url in claim.source_urls:
        art = world.article(url)
        if art:
            _, tier, score = trust_of(url, art["publisher"])
            lines.append(f"{art['publisher']} - Tier {tier} (trust {score:g}) - {art['published'][:10]} - {art['title']} - <{url}>")
        else:
            lines.append(f"{url[:100]} (NOT in the fetched evidence)")
    return lines


def _evidence_strength(world, claim):
    arts = [Article(a["title"], a["publisher"], url, a["published"]) for url in dict.fromkeys(claim.source_urls)
            if (a := world.article(url))]
    return source_profile(arts)["summary"] if arts else "no fetched sources"


def _option_label(kind, share):
    return dec.describe(kind, share)


def _cost_cells(c):
    return f"{dec.gbp(c['worst'])} | {dec.gbp(c['likely'])} | {dec.gbp(c['best'])} | {dec.gbp(c['forecast_wrong'])}"


def _decisions_section(report, passed):
    """Recommended decision per verified claim, the option comparison, and what the Red Team changed. Figures are Python-computed."""
    decs = report.get("decisions") or {}
    if not decs:
        return []
    out = ["", "## Recommended decisions (next quarter)", "",
           f"Options are compared as the change in quarterly cost vs baseline (+ extra cost, - saving). Assumed premiums on the protected "
           f"spend: hedge {dec.PREMIUM_PCT['HEDGE']}%, price lock {dec.PREMIUM_PCT['LOCK_PRICE']}%, second supplier "
           f"{dec.PREMIUM_PCT['DUAL_SOURCE']}% (illustrative, to be replaced by real quotes). \"If forecast wrong\" is the mirror of the "
           "likely case. The model picks and explains an option; every GBP figure is computed in Python.", ""]
    for i in passed:
        wid = i["delegation"]["work_id"]
        d = decs.get(wid)
        if not d:
            continue
        outcome = d["outcome"] or {}
        if not d["versions"]:
            out += [f"### {wid}. {i['attempts'][-1]['claim'].part_name} - decision not produced", "",
                    f"- {outcome.get('error', 'no decision')}", ""]
            continue
        first, final = d["versions"][0], d["versions"][-1]
        impact, spend = i["impact"], i["impact"]["baseline_quarterly_spend_gbp"]
        contested = outcome.get("status") == "CONTESTED"
        claim = i["attempts"][-1]["claim"]
        not_challenged = outcome.get("status") in ("NOT_CHALLENGED", "UNCHALLENGED")
        head = "CONTESTED - needs CFO judgement" if contested else ("Proposed, NOT challenged" if not_challenged else "Recommended")
        out += [f"### {wid}. {claim.part_name} ({claim.supplier_country}) - {head}", ""]
        if contested:
            out += [f"**No confident recommendation.** The Red Team's high-severity objection was still unresolved after {outcome.get('rounds', 0)} "
                    f"round(s). Last proposal: **{_option_label(final['kind'], final['share_pct'])}**.", ""]
        elif not_challenged:
            out += [f"**Unchallenged proposal:** {_option_label(final['kind'], final['share_pct'])} - {final['rationale']}", ""]
        else:
            out += [f"**{_option_label(final['kind'], final['share_pct'])}** - {final['rationale']}", ""]
        rows = dec.compare_options(spend, impact, (final["kind"], final["share_pct"]))
        out += ["| Option | Premium | Worst | Likely | Best | If forecast wrong |", "|---|---|---|---|---|---|"]
        for r in rows:
            mark = " **<- proposed**" if (r["kind"], r["share_pct"]) == (final["kind"], final["share_pct"]) else ""
            out.append(f"| {_option_label(r['kind'], r['share_pct'])}{mark} | {dec.gbp(r['premium_gbp'])} | {_cost_cells(r)} |")

        objs = sorted(d["objections"], key=lambda o: ({"high": 0, "medium": 1, "low": 2}[o["severity"]], int(o["id"][1:])))
        out += ["", "**Challenged by Red Team**", ""]
        if not objs:
            out.append(f"- Not challenged: {outcome['error']}" if outcome.get("error") else
                       "- No line of attack applied: the forecast-wrong, cost-of-acting, concentration, evidence and cheaper-option checks "
                       "(computed in Python) all came back clear." if outcome.get("status") == "RECOMMENDED" else
                       "- The Red Team returned no objection with a specific evidence reference, so this decision went unchallenged.")
        for o in objs[:4]:
            resp = o.get("response") or {}
            tail = f" Decision Maker: {resp['action'].lower()} - {resp['reason']}" if resp.get("action") in ("REVISE", "DEFEND") else ""
            out.append(f"- **{o['severity'].upper()} - {o['type'].replace('_', ' ')}** ({', '.join(o['evidence_refs'])}): {o['text']} "
                       f"-> **{o.get('handling') or handling(o)}**.{tail}")
        if len(objs) > 4:
            out.append(f"- {len(objs) - 4} further lower-severity objection(s) are in the world state.")
        if outcome.get("changed"):
            out += ["", f"**What changed:** first proposal **{_option_label(first['kind'], first['share_pct'])}** became "
                        f"**{_option_label(final['kind'], final['share_pct'])}**.", "",
                    "| | Worst | Likely | Best | If forecast wrong |", "|---|---|---|---|---|",
                    f"| Before ({_option_label(first['kind'], first['share_pct'])}) | {_cost_cells(first['cost'])} |",
                    f"| After ({_option_label(final['kind'], final['share_pct'])}) | {_cost_cells(final['cost'])} |"]
        else:
            out += ["", f"**What changed:** nothing - the first proposal stood ({outcome.get('rounds', 0)} challenge round(s), max {MAX_ROUNDS})."]
        out.append("")
    return out


def console_summary(report):
    lines = ["CFO BRIEF - Ganga AI Solutions (SYNTHETIC DATA) - change in quarterly cost, worst / likely / best"]
    for item in report["items"]:
        d = item["delegation"]
        if d.get("action") != "run":
            continue
        final = item["attempts"][-1]["claim"] if item["attempts"] else None
        tag = f"{d['work_id']} {d['analyst']}"
        if _outcome(item) == "PASS" and item["impact"]:
            i = item["impact"]
            lines.append(f"[PASS, {i['confidence']} confidence] {tag}: {final.part_name} ({final.supplier_country}) "
                         f"{final.direction.replace('_', ' ')} {final.pct_low:g}-{final.pct_high:g}% -> "
                         f"{gbp(i['worst_gbp'])} / {gbp(i['likely_gbp'])} / {gbp(i['best_gbp'])}")
            lines.append(f"    {final.claim}")
        elif _outcome(item) == "UNVERIFIED":
            lines.append(f"[UNVERIFIED] {tag}: {d['part_id']} not quantified. {d.get('note') or ''}".rstrip())
        elif _outcome(item) == "NO_CLAIM":
            lines.append(f"[NO CLAIM] {tag}: {d['part_id']} - {d.get('note', '')}")
        else:
            lines.append(f"[NOT ASSESSED] {tag}: {d['part_id']} - {d.get('error', 'model call failed')}")
    for wid, d in sorted((report.get("decisions") or {}).items()):
        if not d["versions"]:
            continue
        o, final = d["outcome"] or {}, d["versions"][-1]
        lines.append(f"[DECISION {o.get('status', '?')}] {wid}: {_option_label(final['kind'], final['share_pct'])} "
                     f"({o.get('objection_count', 0)} objection(s), {o.get('rounds', 0)} round(s)"
                     f"{', revised' if o.get('changed') else ''})")
    return "\n".join(lines)


def build_brief(report, world, trace_path, demo_weak_claim, stats, demo_untrusted_source=False, demo_red_team=False):
    items = [i for i in report["items"] if i["delegation"].get("action") == "run"]
    skipped = [i["delegation"] for i in report["items"] if i["delegation"].get("action") == "skip"]
    passed = [i for i in items if _outcome(i) == "PASS" and i["impact"]]
    plan, review, run = report["plan"], report["review"], report["run"]
    out = [
        "# Vidhur CFO Brief - Ganga AI Solutions", "",
        "> **SYNTHETIC DATA.** Ganga AI Solutions is a fictional company. Its purchase history is made up for the "
        "Open Agent Hackathon 2026. News and exchange-rate evidence is real, fetched live from free sources.", "",
        f"- Run `{run.get('run_id', '')}` - {run.get('elapsed_seconds', '?')} s - team: Planner (Vidhur), Geopolitics Analyst, Currency Analyst, Evaluator",
        f"- Shared memory (Meterless World Model, {report['event_count']} events): `{report['path'].replace(chr(92), '/').split('/vidhur/')[-1]}`",
        f"- Trace log (every step, model, tokens, latency): `{str(trace_path).replace(chr(92), '/').split('/vidhur/')[-1]}`",
        f"- Model calls: {stats['llm_ok']} succeeded, {stats['llm_fail']} failed attempts (retried automatically)",
        "- Baseline: Ganga's spend in the latest quarter, read from Zetaris with SQL.",
    ]
    if demo_weak_claim:
        out += [f"- **Demo flag `--demo-weak-claim` was on.** The Geopolitics Analyst's first attempt was given this extra instruction: "
                f"*\"{DEMO_WEAK_CLAIM_INSTRUCTION}\"* The model calls and Evaluator checks are real."]

    if demo_untrusted_source:
        out += [f"- **Demo flag `--demo-untrusted-source` was on.** The Geopolitics Analyst's first attempt was shown the least-trusted articles "
                f"first and given this extra instruction: *\"{DEMO_UNTRUSTED_INSTRUCTION}\"* The model calls and Evaluator checks are real."]

    if demo_red_team:
        out += [f"- **Demo flag `--demo-red-team` was on.** The first decision for the lowest-confidence verified claim was given this extra "
                f"instruction: *\"{DEMO_AGGRESSIVE_INSTRUCTION}\"* The model calls, the Red Team and the challenge loop are real."]

    out += ["", "## The Planner's plan", "", plan.get("summary", "")]
    if plan.get("fallback"):
        out += ["", "**The planning model's reply was unusable, so this is the documented fallback plan.**"]
    out += ["", "| Work | Analyst | Part | Why the Planner chose it |", "|---|---|---|---|"]
    for i in report["items"]:
        d = i["delegation"]
        if d.get("action") == "run":
            out.append(f"| {d['work_id']}{' (re-delegation)' if d.get('redelegation') else ''} | {d['analyst']} | {d['part_id']} | {d.get('reason', '')} |")
    for d in skipped:
        out.append(f"| skipped | {d.get('analyst', '')} | {d.get('part_id', '')} | {d.get('reason', '')} |")
    if review:
        out += ["", f"**Planner's review after the Evaluator stage:** "
                    f"{'re-delegated ' + review.get('analyst', '') + ' for ' + review.get('part_id', '') + ' - ' if review.get('redelegate') else 'no re-delegation - '}"
                    f"{review.get('reason', '')}"]

    out += ["", "## Verified impact (change in quarterly cost vs latest-quarter spend; + is extra cost, - is a saving)", ""]
    if passed:
        out += ["| Work | Part | Supplier country | Direction | Est. change | Worst | Likely | Best | Confidence |", "|---|---|---|---|---|---|---|---|---|"]
        for i in passed:
            c, imp = i["attempts"][-1]["claim"], i["impact"]
            out.append(f"| {i['delegation']['work_id']} ({c.analyst}) | {c.part_name} | {c.supplier_country} | {c.direction.replace('_', ' ')} | "
                       f"{c.pct_low:g}-{c.pct_high:g}% | {gbp(imp['worst_gbp'])} | {gbp(imp['likely_gbp'])} | {gbp(imp['best_gbp'])} | {imp['confidence']} |")
        total = [sum(i["impact"][k] for i in passed) for k in ("worst_gbp", "likely_gbp", "best_gbp")]
        out += ["", f"**Sum of verified claims: worst {gbp(total[0])} / likely {gbp(total[1])} / best {gbp(total[2])} per quarter** "
                    "(claims on the same part from different analysts may overlap)."]
    else:
        out.append("No claim passed the Evaluator in this run, so no GBP impact is reported.")

    out += _decisions_section(report, passed)

    out += ["", "## Evidence trail", ""]
    for i in items:
        d = i["delegation"]
        out += [f"### {d['work_id']}. {d['analyst']} on {d['part_id']} - **{_outcome(i)}**", "", f"_Planner's reason: {d.get('reason', '')}_", ""]
        if not i["attempts"]:
            out += [f"- No claim: {d.get('note') or d.get('error') or 'n/a'}", ""]
            continue
        c = i["attempts"][-1]["claim"]
        out += [c.claim, "", f"- Estimate: {c.direction.replace('_', ' ')} {c.pct_low:g}-{c.pct_high:g}% "
                f"({'computed in Python from ECB rates' if c.analyst == 'currency' else 'analyst estimate, not a source figure'})"]
        if c.computed:
            out.append(f"- FX data: {c.computed['currency']} {c.computed['move_30d_pct']:+.2f}% over 30 days, {c.computed['move_90d_pct']:+.2f}% over 90 days "
                       f"({c.computed['start_date']} to {c.computed['end_date']}); pass-through assumed {c.computed['pass_through']}")
        if i["impact"]:
            imp = i["impact"]
            out.append(f"- GBP: baseline {imp['quarter']} spend £{imp['baseline_quarterly_spend_gbp']:,.0f}; worst {gbp(imp['worst_gbp'])}, likely "
                       f"{gbp(imp['likely_gbp'])}, best {gbp(imp['best_gbp'])}; confidence **{imp['confidence']}** (computed in Python from Zetaris data)")
        out.append(f"- Reasoning: {c.reasoning}")
        out += [f"- Evidence strength: **{_evidence_strength(world, c)}** (publisher tiers from `data/source_trust.csv`)"]
        out += ["- Sources cited in the final attempt (publisher, tier, date):"] + [f"  - {s}" for s in _sources(world, c)]
        out.append("- Audit history (from the world state):")
        for a in i["attempts"]:
            for v in a["verdicts"]:
                out.append(f"  - Attempt {a['claim'].attempt}: **{v['verdict']}** ({v['independent_sources']} independent source(s)) - {' '.join(v['reasons'])}")
        if d.get("note") and _outcome(i) != "PASS":
            out.append(f"  - Outcome: {d['note']}")
        out.append("")

    others = [i for i in items if not i["attempts"]]
    if others:
        out += ["## No claim / not assessed", ""]
        for i in others:
            d = i["delegation"]
            out.append(f"- {d['work_id']} {d['analyst']} on {d['part_id']}: {_outcome(i)} - {d.get('note') or d.get('error') or ''}")
        out.append("")
    if any(_outcome(i) == "UNVERIFIED" for i in items):
        out += ["## Note", "", "UNVERIFIED claims are listed in the evidence trail for transparency. They have no GBP figure and must not be acted on.", ""]
    return "\n".join(out) + "\n"
