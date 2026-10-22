"""Vidhur - Day 1 run: Zetaris -> Geopolitics Analyst -> Evaluator loop -> GBP impact -> CFO brief.

Usage:
    python main.py
    python main.py --demo-weak-claim    # analyst's first attempt is told to cite only ONE source,
                                        # so you can watch the Evaluator catch it and send it back
"""

import argparse
import sys
from datetime import datetime

import openai

from agents.evaluator import Evaluator, audit_claim
from agents.geopolitics_analyst import DEMO_WEAK_CLAIM_INSTRUCTION, GeopoliticsAnalyst
from core import config
from core.ganga_data import part_country_catalog, quarterly_spend, top_targets
from core.impact import compute_impact, confidence_level
from core.trace import Trace
from core.zetaris_client import ZetarisClient, ZetarisError

OUTPUT_DIR = config.ROOT / "output_examples"


def parse_args():
    p = argparse.ArgumentParser(description="Vidhur Day 1: geopolitics cost-risk run with an auditing Evaluator.")
    p.add_argument("--demo-weak-claim", action="store_true",
                   help="DEMO: instruct the analyst's first attempt to cite only one source so the Evaluator visibly "
                        "catches it. Real model calls and a real Evaluator check; only the analyst's instruction changes.")
    p.add_argument("--targets", type=int, default=3, help="How many top-spend part/country pairs to examine (default 3).")
    p.add_argument("--refresh-news", action="store_true", help="Ignore the news cache and fetch live.")
    return p.parse_args()


def gbp(value):
    return f"{'+' if value > 0 else '-' if value < 0 else ''}£{abs(value):,.0f}"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    trace = Trace()
    print(f"Vidhur Day 1 | run {trace.run_id} | trace: {trace.path}")
    if args.demo_weak_claim:
        print("DEMO MODE --demo-weak-claim: analyst's first attempt is instructed to cite only one source.")

    # 1. Zetaris health check
    zt = ZetarisClient(trace=trace)
    if not zt.health_check():
        sys.exit("Zetaris health check failed. Is Docker Desktop running and the Zetaris containers healthy?")
    trace.event("main", "zetaris:health", output_summary="SELECT 1 returned 1", decision="OK")

    # 2. Pick targets from Zetaris
    targets = top_targets(zt, args.targets)
    catalog = part_country_catalog(zt)
    trace.event("main", "targets", output_summary="; ".join(f"{t.part_id} {t.country} £{t.quarterly_spend_gbp:,.0f}" for t in targets))

    # 3. Analyst + Evaluator loop
    analyst = GeopoliticsAnalyst(trace, demo_weak_claim=args.demo_weak_claim, refresh_news=args.refresh_news)
    evaluator = Evaluator(catalog, analyst.pool, trace)

    audited, no_claim, errored = [], [], []
    for i, target in enumerate(targets, 1):
        try:
            claim = analyst.draft_claim(target, claim_id=f"C{i}")
            if claim is None:
                no_claim.append(target)
                continue
            result = audit_claim(claim, target, analyst, evaluator, trace)
        except (openai.OpenAIError, ZetarisError) as exc:
            # A model outage on one target must not lose the others; it is reported, never hidden.
            trace.event("main", "target:error", input_summary=f"{target.part_id} {target.country}",
                        output_summary=f"{type(exc).__name__}: {str(exc)[:250]}", decision="NOT_ASSESSED")
            errored.append((target, f"{type(exc).__name__}"))
            continue
        impact = confidence = None
        if result.status == "PASS":
            spend, quarter = quarterly_spend(zt, result.claim.part_id)
            impact = compute_impact(spend, result.claim.pct_low, result.claim.pct_high, result.claim.direction)
            confidence = confidence_level(result.verdict.independent_sources, result.retries, result.verdict.strength)
            trace.event("impact", "gbp", input_summary=f"{result.claim.part_id} {quarter} spend £{spend:,.2f}",
                        output_summary=f"worst {gbp(impact.worst_gbp)} / likely {gbp(impact.likely_gbp)} / best {gbp(impact.best_gbp)}; confidence {confidence}")
        audited.append((target, result, impact, confidence))

    # 4. Brief
    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    brief = build_brief(audited, no_claim, errored, trace, args, analyst.pool)
    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"brief_{run_stamp}.md"
    out_path.write_text(brief, encoding="utf-8")

    print("\n" + "=" * 78)
    print(console_summary(audited, no_claim, errored))
    print("=" * 78)
    print(f"Brief saved: {out_path}")
    print(f"Trace file:  {trace.path}")


# --------------------------------------------------------------------------- output
def console_summary(audited, no_claim, errored):
    lines = ["CFO BRIEF - Ganga AI Solutions (SYNTHETIC DATA) - change in quarterly cost, worst / likely / best"]
    for target, result, impact, confidence in audited:
        c = result.claim
        if result.status == "PASS":
            lines.append(f"[PASS, {confidence} confidence] {c.part_name} ({c.supplier_country}): {c.direction.replace('_', ' ')} "
                         f"{c.pct_low:g}-{c.pct_high:g}% -> {gbp(impact.worst_gbp)} / {gbp(impact.likely_gbp)} / {gbp(impact.best_gbp)}")
            lines.append(f"    {c.claim}")
            lines.append(f"    Evaluator: {result.verdict.independent_sources} independent sources"
                         + (f", passed after {result.retries} send-back(s)" if result.retries else ", passed first time"))
        else:
            lines.append(f"[UNVERIFIED] {target.part_name} ({target.country}): not quantified. {result.verdict.reasons[0][:200]}")
    for target in no_claim:
        lines.append(f"[NO CLAIM] {target.part_name} ({target.country}): no recent news that affects its cost.")
    for target, why in errored:
        lines.append(f"[NOT ASSESSED] {target.part_name} ({target.country}): model call failed ({why}); see trace.")
    return "\n".join(lines)


def build_brief(audited, no_claim, errored, trace, args, pool):
    passed = [a for a in audited if a[1].status == "PASS"]
    unverified = [a for a in audited if a[1].status != "PASS"]
    out = [
        "# Vidhur CFO Brief - Ganga AI Solutions",
        "",
        "> **SYNTHETIC DATA.** Ganga AI Solutions is a fictional company. Its purchase history is made up for the "
        "Open Agent Hackathon 2026. The news evidence is real and was fetched live from free RSS feeds.",
        "",
        f"- Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} (run `{trace.run_id}`)",
        f"- Scope: Geopolitics Analyst only (Day 1). Other analysts, the Planner and the Red Team are not built yet.",
        f"- Trace log (every step, model, tokens, latency): `{trace.path.relative_to(config.ROOT).as_posix()}`",
        f"- Baseline: Ganga's spend in the latest quarter, read from Zetaris with SQL.",
    ]
    if args.demo_weak_claim:
        out += [f"- **Demo flag `--demo-weak-claim` was on.** The analyst's first attempt was given this extra instruction: "
                f"*\"{DEMO_WEAK_CLAIM_INSTRUCTION}\"* The model calls and the Evaluator checks are real.", ""]
    out += ["", "## Verified impact (change in quarterly cost vs latest-quarter spend; + is extra cost, - is a saving)", ""]
    if passed:
        out += ["| Part | Supplier country | Direction | Est. change | Worst | Likely | Best | Confidence |", "|---|---|---|---|---|---|---|---|"]
        for _, r, imp, conf in passed:
            c = r.claim
            out.append(f"| {c.part_name} | {c.supplier_country} | {c.direction.replace('_', ' ')} | {c.pct_low:g}-{c.pct_high:g}% | "
                       f"{gbp(imp.worst_gbp)} | {gbp(imp.likely_gbp)} | {gbp(imp.best_gbp)} | {conf} |")
        total = [sum(getattr(a[2], k) for a in passed) for k in ("worst_gbp", "likely_gbp", "best_gbp")]
        out += ["", f"**Total across verified claims: worst {gbp(total[0])} / likely {gbp(total[1])} / best {gbp(total[2])} per quarter.**"]
    else:
        out.append("No claim passed the Evaluator in this run, so no GBP impact is reported.")

    out += ["", "## Evidence trail", ""]
    for target, r, imp, conf in audited:
        c = r.claim
        out += [f"### {c.claim_id}. {c.part_name} - {c.supplier_country} - **{r.status}**", "", c.claim, ""]
        out.append(f"- Estimate: {c.direction.replace('_', ' ')} {c.pct_low:g}-{c.pct_high:g}% (analyst's own estimate, not a source figure)")
        if imp:
            out.append(f"- GBP: baseline {target.quarter} spend £{imp.baseline_quarterly_spend_gbp:,.0f}; worst {gbp(imp.worst_gbp)}, "
                       f"likely {gbp(imp.likely_gbp)}, best {gbp(imp.best_gbp)}; confidence **{conf}** (computed in Python from Zetaris data)")
        out.append(f"- Analyst reasoning: {c.reasoning}")
        out.append(f"- Evaluator ({r.verdict.independent_sources} independent source(s)): {' '.join(r.verdict.reasons)}")
        out += ["- Sources cited in the final attempt:"]
        for url in c.source_urls:
            out.append(f"  - {describe_source(pool, url)}")
        if len(r.history) > 1:
            out += ["- Audit history:"]
            for h in r.history:
                v = h["verdict"]
                out.append(f"  - Attempt {h['attempt']}: **{v['verdict']}** - {' '.join(v['reasons'])}")
        out.append("")

    if no_claim:
        out += ["## No claim", ""] + [f"- {t.part_name} ({t.country}): the analyst found no recent development that affects its cost." for t in no_claim] + [""]
    if errored:
        out += ["## Not assessed (technical error)", ""] + [f"- {t.part_name} ({t.country}): the model call failed ({why}). Nothing is claimed about it; see the trace." for t, why in errored] + [""]
    if unverified:
        out += ["## Note", "", "UNVERIFIED claims are listed in the evidence trail above for transparency. They have no GBP figure and must not be acted on.", ""]
    return "\n".join(out) + "\n"


def describe_source(pool, url):
    art = pool.get(url)
    if art is None:
        return f"{url[:100]} (NOT in the fetched news set)"
    return f"{art.publisher} - {art.published[:10]} - {art.title} - <{art.url}>"


if __name__ == "__main__":
    main()
