"""The CFO brief. Built ONLY from the world-state report (RunWorld.report()), never from local variables."""

from agents.geopolitics_analyst import DEMO_WEAK_CLAIM_INSTRUCTION


def gbp(value):
    return f"{'+' if value > 0 else '-' if value < 0 else ''}£{abs(value):,.0f}"


def _outcome(item):
    return item["delegation"].get("status", "?")


def _sources(world, claim):
    lines = []
    for url in claim.source_urls:
        art = world.article(url)
        lines.append(f"{art['publisher']} - {art['published'][:10]} - {art['title']} - <{url}>" if art
                     else f"{url[:100]} (NOT in the fetched evidence)")
    return lines


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
    return "\n".join(lines)


def build_brief(report, world, trace_path, demo_weak_claim, stats):
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
        out += ["- Sources cited in the final attempt:"] + [f"  - {s}" for s in _sources(world, c)]
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
