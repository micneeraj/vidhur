"""Vidhur - Day 2 run.

Zetaris health -> Planner plans -> analysts work in parallel -> Evaluator audits (send-back loop)
-> Planner reviews and may re-delegate once -> GBP impact -> CFO brief. Every agent shares one
Meterless World Model (logs/world_state_<run_id>.json); the brief is built from it.

Usage:
    python main.py
    python main.py --demo-red-team      # the first decision is told to be aggressive so the Red Team visibly challenges it
    python main.py --demo-weak-claim    # the Geopolitics Analyst's first attempt is told to cite only ONE source,
                                        # so you can watch the Evaluator catch it and send it back
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import openai
import requests

from agents.currency_analyst import CurrencyAnalyst
from agents.decision_maker import run_decisions
from agents.evaluator import Evaluator
from agents.geopolitics_analyst import GeopoliticsAnalyst
from agents.planner import Planner
from agents.workflow import run_work_item
from core import config
from core import failures
from core.brief import build_brief, console_summary
from core.ganga_data import part_country_catalog, top_targets
from core.preflight import RUNTIME_BUSY_EARLY, RUNTIME_BUSY_MESSAGE, ZETARIS_START_HINT, run_checks
from core.shared_state import RunWorld
from core.trace import Trace
from core.zetaris_client import ZetarisClient, ZetarisError, ZetarisUnreachable

# A service being down or busy (after the retries inside core/llm.py and the Zetaris client) is reported, never a traceback.
SERVICE_ERRORS = (openai.OpenAIError, ZetarisError, requests.RequestException)
EXIT_STOPPED_EARLY, EXIT_PARTIAL = 1, 2

OUTPUT_DIR = config.ROOT / "output_examples"
INPUT_DIR = config.ROOT / "input_examples"


def parse_args():
    p = argparse.ArgumentParser(description="Vidhur Day 2: Planner + analysts + auditing Evaluator over a shared World Model.")
    p.add_argument("--demo-weak-claim", action="store_true",
                   help="DEMO: instruct the Geopolitics Analyst's first attempt to cite only one source so the Evaluator visibly "
                        "catches it. Real model calls and a real Evaluator check; only the analyst's instruction changes.")
    p.add_argument("--demo-untrusted-source", action="store_true",
                   help="DEMO: the Geopolitics Analyst's first attempt is shown the least-trusted articles first and told to cite only "
                        "the first two, so the Evaluator visibly sends it back (NO_TRUSTED_SOURCE / SOURCES_NOT_INDEPENDENT). "
                        "Real model calls and a real Evaluator check.")
    p.add_argument("--demo-red-team", action="store_true",
                   help="DEMO: the first proposed decision for the lowest-confidence verified claim is instructed to be aggressive "
                        "(hedge 100%%), so the Red Team visibly objects and the Decision Maker revises or the decision ends CONTESTED. "
                        "Real model calls and a real challenge loop.")
    p.add_argument("--workers", type=int, default=3, help="Analysts running in parallel (default 3).")
    p.add_argument("--refresh-news", action="store_true", help="Ignore the news cache and fetch live.")
    p.add_argument("--skip-healthcheck", action="store_true", help="Do not run the start-up checks (same as scripts/healthcheck.py).")
    return p.parse_args()


def preflight():
    """The same checks as scripts/healthcheck.py. Returns True if the run may start."""
    checks = run_checks()
    for c in checks:
        print(c.line())
    if all(c.ok for c in checks):
        return True
    print("\nStopped before the run: fix the FAIL line(s) above. Nothing was run and nothing was saved. (scripts/healthcheck.py repeats these checks.)")
    if any(not c.ok and c.name.startswith("Nightshift") and "busy" in c.detail for c in checks):
        print(RUNTIME_BUSY_EARLY)
    return False


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    if not args.skip_healthcheck and not preflight():
        sys.exit(EXIT_STOPPED_EARLY)
    started = time.monotonic()
    trace = Trace()
    world = RunWorld(trace.run_id)
    print(f"Vidhur Day 2 | run {trace.run_id} | trace: {trace.path} | world state: {world.path}")
    if args.demo_red_team:
        print("DEMO MODE --demo-red-team: the first decision for the lowest-confidence verified claim is instructed to be aggressive.")
    if args.demo_untrusted_source:
        print("DEMO MODE --demo-untrusted-source: the Geopolitics Analyst's first attempt is shown the least-trusted articles first.")
    if args.demo_weak_claim:
        print("DEMO MODE --demo-weak-claim: the Geopolitics Analyst's first attempt is instructed to cite only one source.")

    # 1. Zetaris health check, then Ganga's spend by part (all parts, largest first)
    zt = ZetarisClient(trace=trace)
    try:
        if not zt.health_check():
            sys.exit("Zetaris health check failed. Is Docker Desktop running and the Zetaris containers healthy?")
        trace.event("main", "zetaris:health", output_summary="SELECT 1 returned 1", decision="OK")
        targets = top_targets(zt, 99)
        catalog = part_country_catalog(zt)
    except ZetarisUnreachable as exc:
        sys.exit(f"{exc}\n{ZETARIS_START_HINT}")
    except ZetarisError as exc:
        sys.exit(f"Zetaris problem: {exc}")
    by_part = {t.part_id: t for t in targets}
    world.start_run({"run_id": trace.run_id, "demo_weak_claim": args.demo_weak_claim, "demo_untrusted_source": args.demo_untrusted_source, "demo_red_team": args.demo_red_team, "started": datetime.now().isoformat(timespec="seconds"),
                     "quarter": targets[0].quarter})

    # The run's input, saved next to its brief (no secrets: lane ids and flags only)
    INPUT_DIR.mkdir(exist_ok=True)
    input_path = INPUT_DIR / f"input_{trace.path.stem.removeprefix('run_')}.json"
    input_path.write_text(json.dumps({
        "command": "python main.py" + "".join(f" {f}" for f in sys.argv[1:]), "run_id": trace.run_id,
        "flags": {"demo_weak_claim": args.demo_weak_claim, "demo_untrusted_source": args.demo_untrusted_source,
                  "demo_red_team": args.demo_red_team, "workers": args.workers, "refresh_news": args.refresh_news},
        "config": {"analyst_lane": config.analyst_model(), "evaluator_planner_lane": config.evaluator_model(),
                   "evidence_max_age_days": config.evidence_max_age_days()},
        "ganga_spend_from_zetaris": [t.__dict__ for t in targets], "trace_file": f"logs/{trace.path.name}",
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    # 2. The team
    pool = {}  # url -> Article: everything fetched this run
    analysts = {
        "geopolitics": GeopoliticsAnalyst(trace, world, pool, demo_weak_claim=args.demo_weak_claim, refresh_news=args.refresh_news,
                                          demo_untrusted_source=args.demo_untrusted_source),
        "currency": CurrencyAnalyst(trace, world, pool, refresh_news=args.refresh_news),
    }
    evaluator = Evaluator(catalog, pool, world, trace)
    planner = Planner(trace, world)

    stopped = []  # stages that a service outage cut short: {agent, stage, error}

    def guarded(agent, stage, fn):
        """Run one stage. A service outage is recorded (trace + world state + brief) and the run carries on with what it has."""
        try:
            return fn()
        except SERVICE_ERRORS as exc:
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
            stopped.append({"agent": agent, "stage": stage, "error": error})
            trace.event("main", f"stopped:{stage}", output_summary=f"{agent} could not run - {error}", decision="STOPPED")
            return None

    # 3. Planner decides which analyst works on which part, and why
    plan = guarded("Planner", "planning", lambda: planner.plan(targets))
    delegations = plan["delegations"] if plan else []

    # 4. Analysts work in parallel; each work item runs draft -> Evaluator loop -> impact
    def process(delegation):
        try:
            run_work_item(delegation, by_part[delegation["part_id"]], analysts, evaluator, world, zt, trace)
        except SERVICE_ERRORS as exc:  # run_work_item reports its own; this is the safety net
            world.write_error(delegation["work_id"], f"{type(exc).__name__}: {str(exc)[:200]}")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        list(executor.map(process, delegations))

    # 5. Planner reviews the world state and may re-delegate once
    if plan:
        extra = guarded("Planner", "review", lambda: planner.review(world.report(), targets))
        if extra:
            process(extra)

    # 6. Decision Maker proposes a decision per verified claim; the Red Team challenges it (max 2 rounds)
    if plan:
        guarded("Decision Maker / Red Team", "decisions", lambda: run_decisions(
            world.report(), world, targets, trace, demo_red_team=args.demo_red_team, workers=min(args.workers, 2)))

    # 7. Brief, built from the world state (also when partial: it lists which agents could not run and why)
    elapsed = round(time.monotonic() - started)
    summary = {"elapsed_seconds": elapsed, "llm_ok": trace.stats["llm_ok"], "llm_fail": trace.stats["llm_fail"]}
    if stopped:
        summary["stopped"] = stopped
    report = world.report()
    partial = bool(failures.collect({**report, "run": summary}))
    world.finish_run(summary, status="partial" if partial else "finished")
    report = world.report()
    brief = build_brief(report, world, trace.path, args.demo_weak_claim, trace.stats, args.demo_untrusted_source,
                        args.demo_red_team)
    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / f"brief_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out_path.write_text(brief, encoding="utf-8")

    print("\n" + "=" * 78)
    print(console_summary(report))
    print("=" * 78)
    print(f"Run time: {elapsed} s | model calls: {trace.stats['llm_ok']} ok, {trace.stats['llm_fail']} failed attempts")
    print(f"Brief saved:     {out_path}")
    print(f"Trace file:      {trace.path}")
    print(f"World state:     {world.path} ({report['event_count']} events)")
    if partial:
        print("\n" + RUNTIME_BUSY_MESSAGE)
        for f in failures.collect(report):
            print(f"  - {f['agent']} ({f['where']}): {f['reason']}")
        sys.exit(EXIT_PARTIAL)


if __name__ == "__main__":
    main()
