"""Small backtest of Vidhur against two simpler approaches, on 5 historical cases.

For each case (data/eval/backtest_cases.csv) the question is: in which direction does Ganga's landed cost in GBP move over the
next ~quarter (91 days)? The real outcome is computed in Python from REAL historical ECB exchange rates (Frankfurter) and
Ganga's SYNTHETIC purchase mix (the last quarter completed before as_of, read from Zetaris):

    outcome % = sum over parts of  spend share  x  (GBP cost of the supplier's currency at as_of+91d / at as_of - 1) x 100
    up if > +2 %, down if < -2 %, otherwise flat.   100 % pass-through of the supplier currency, as in the Currency Analyst.
    Taiwan's dollar is not published by the ECB, so Taiwan (the GPU chip supplier) is proxied by USD.

Three approaches see the SAME inputs (spend by part at as_of, headlines dated <= as_of, ECB moves ending <= as_of):
  a) naive      always predicts "flat"
  b) model only ONE Nemotron call through Nightshift (the analyst lane): no Planner, no Evaluator, no Red Team
  c) Vidhur     Planner -> analysts -> Evaluator (send-back loop) -> Planner review -> GBP impact from verified claims.
                The Decision Maker / Red Team stage is NOT run here: it works on hedging choices after the cost forecast and never changes it.

Vidhur's forecast = sum of the verified claims' GBP impacts (likely) over the quarter's total spend; no verified claim = predicts flat.
Its GBP range = [sum of best cases, sum of worst cases]; "contained" = the real outcome in GBP lies inside it.
A case that hit a runtime error is reported as NOT COMPLETED - neither right nor wrong.

No data dated after as_of reaches an approach: core/asof.py raises AsOfViolation (news, FX window, Zetaris quarters).
Caveat nobody can remove: the model may remember these historical events from training.

Usage:  python scripts/eval_backtest.py            (LLM_MAX_ATTEMPTS=4 LLM_RETRY_WAIT_SECONDS=60 recommended)
        python scripts/eval_backtest.py --previous logs/backtest_results_<day>.jsonl --only B2,B3,B4,B5
        (continue an interrupted run: finished cases are read back from the earlier results file, never re-run)
Output: output_examples/eval_backtest.md, logs/backtest_*.jsonl (+ world states).
"""

import csv
import json
import os
import sys
from contextlib import ExitStack
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.chdir(Path(__file__).resolve().parents[1])

from core import asof, config  # noqa: E402
from core.evalutil import llm_summary  # noqa: E402

CASES_CSV = config.ROOT / "data" / "eval" / "backtest_cases.csv"
REPORT = config.ROOT / "output_examples" / "eval_backtest.md"
HORIZON_DAYS = 91
FLAT_BAND_PCT = 2.0
# Currency each supplier country is paid in for the OUTCOME. Taiwan -> USD is a proxy (the ECB has no TWD).
OUTCOME_CURRENCY = {"Taiwan": "USD", "South Korea": "KRW", "Japan": "JPY", "China": "CNY", "Germany": "EUR", "Malaysia": "MYR"}
FRANKFURTER = "https://api.frankfurter.dev/v1"


# --------------------------------------------------------------------------- pure helpers (unit tested, no network)
def label_direction(pct, band=FLAT_BAND_PCT):
    return "up" if pct > band else "down" if pct < -band else "flat"


def outcome_pct(spend_by_currency, start_rates, end_rates):
    """Weighted % change in the GBP cost of the purchase mix. Rates are units of currency per 1 GBP (fewer units = dearer currency)."""
    total = sum(spend_by_currency.values())
    if total <= 0:
        raise ValueError("no spend")
    return sum(v / total * (start_rates[c] / end_rates[c] - 1) * 100 for c, v in spend_by_currency.items())


def spend_by_currency(targets):
    out = {}
    for t in targets:
        c = OUTCOME_CURRENCY[t.country]
        out[c] = out.get(c, 0.0) + t.quarterly_spend_gbp
    return out


def aggregate_vidhur(report, total_spend):
    """The portfolio forecast from a finished Vidhur world report: verified claims only."""
    likely = worst = best = 0.0
    verified = []
    for item in report["items"]:
        imp = item["impact"]
        if imp is None:
            continue
        likely += imp["likely_gbp"]
        worst += imp["worst_gbp"]
        best += imp["best_gbp"]
        verified.append({"work_id": item["delegation"]["work_id"], "analyst": item["delegation"]["analyst"],
                         "part_id": item["delegation"]["part_id"], "direction": imp["direction"],
                         "likely_gbp": imp["likely_gbp"], "confidence": imp["confidence"]})
    pct = likely / total_spend * 100
    return {"n_verified": len(verified), "verified": verified, "pct": round(pct, 2), "direction": label_direction(pct),
            "range_gbp": [round(best, 2), round(worst, 2)]}


def contains(range_gbp, outcome_gbp, n_verified):
    """True/False, or None when there was nothing verified to give a range."""
    if n_verified == 0:
        return None
    lo, hi = sorted(range_gbp)
    return lo <= outcome_gbp <= hi


def incomplete_reason(report, stopped):
    """Why a Vidhur run did not complete (a runtime error somewhere), or None when it did."""
    if stopped:
        return "; ".join(f"{s['stage']}: {s['error']}" for s in stopped)
    for item in report["items"]:
        d = item["delegation"]
        if d.get("action") == "run" and d.get("status") == "NOT_ASSESSED":
            return f"{d['work_id']} {d['analyst']}/{d['part_id']}: {d.get('error', 'runtime error')}"
    return None


def parse_model_only(data):
    """(pct_low, pct_high) signed, from the model-only reply, or None when unusable."""
    try:
        lo, hi = float(data["change_pct_low"]), float(data["change_pct_high"])
    except (KeyError, TypeError, ValueError):
        return None
    return (lo, hi) if lo <= hi else (hi, lo)


def model_only_prompt(targets, fx_lines, headlines, as_of):
    total = sum(t.quarterly_spend_gbp for t in targets)
    rows = "\n".join(f"{t.part_id} | {t.part_name} | {t.country} | GBP {t.quarterly_spend_gbp:,.0f} ({t.quarterly_spend_gbp / total:.0%})"
                     for t in targets)
    news = "\n".join(f"{a.published[:10]} | {a.publisher} | {a.title}" for a in headlines) or "(no headlines)"
    fx = "\n".join(fx_lines) or "(none)"
    return (f"Today is {as_of}. Ganga AI Solutions is a UK maker of GPU AI servers that buys parts from suppliers abroad and pays in "
            f"pounds-equivalent. Spend by part in the last completed quarter:\n{rows}\n\nExchange-rate moves against the pound "
            f"(ECB data up to today):\n{fx}\n\nHeadlines up to today:\n{news}\n\n"
            "Estimate the change in Ganga's TOTAL landed cost in GBP over the next quarter (about 91 days) as a signed percentage range "
            "(positive = cost up, negative = cost down). Reply with a single JSON object and nothing else: "
            '{"change_pct_low": <number>, "change_pct_high": <number>, "reason": "<one sentence>"}')


def score(rows):
    """Per approach: right / wrong / not completed, and range containment, over the case rows."""
    out = {}
    for name in ("naive", "model_only", "vidhur"):
        recs = [r[name] for r in rows]
        done = [r for r in recs if r["status"] == "completed"]
        out[name] = {"cases": len(recs), "completed": len(done), "not_completed": len(recs) - len(done),
                     "correct": sum(r["correct"] for r in done),
                     "range_checked": sum(r.get("contained") is not None for r in done),
                     "range_contained": sum(bool(r.get("contained")) for r in done)}
    return out


def render_report(rows, meta):
    s = score(rows)
    L = ["# Backtest: Vidhur vs simpler approaches", "",
         "**5 cases, one run each, a small sample. Company data is synthetic (Ganga AI Solutions); market data (exchange rates) is real "
         "and historical; news is a small prepared set of real dated headlines.** This is a sanity check, not a performance claim.", "",
         f"- Run started {meta['started']} | Nemotron lane `{meta['lane']}` | retries: {meta['retry']}",
         "- Question per case: does Ganga's landed cost in GBP go **up, down or flat (within +/-2 %)** over the 91 days after `as_of`?",
         "- Real outcome: real ECB rates (Frankfurter) x Ganga's synthetic purchase mix for the last quarter completed before `as_of`; "
         "100 % pass-through of the supplier's currency; Taiwan (GPU chips) proxied by USD because the ECB does not publish TWD.",
         "- Data after `as_of` is blocked in code (`core/asof.py`). The model may still remember these events from training.",
         "- Cases chosen: three dates named in the brief plus two calmer quarters, picked from the computed outcomes only (before any approach ran) "
         "so that not every case is \"cost up\".", "",
         "## Results", "",
         "| Case | as_of | Real outcome | Naive (always flat) | Model only | Vidhur | Vidhur GBP range | Real outcome in range? |",
         "|---|---|---|---|---|---|---|---|"]

    def cell(a):
        if a["status"] != "completed":
            return "not completed"
        return f"{a['direction']} ({'right' if a['correct'] else 'wrong'})"

    for r in rows:
        v = r["vidhur"]
        rng = f"£{v['range_gbp'][0]:+,.0f} to £{v['range_gbp'][1]:+,.0f}" if v["status"] == "completed" and v["n_verified"] else "-"
        cont = {True: "yes", False: "no", None: "n/a (no verified claim)"}[v.get("contained")] if v["status"] == "completed" else "not completed"
        L.append(f"| {r['case_id']} {r['theme']} | {r['as_of']} | {r['outcome']['direction']} ({r['outcome']['pct']:+.2f} %, £{r['outcome']['gbp']:+,.0f}) | "
                 f"{cell(r['naive'])} | {cell(r['model_only'])} | {cell(v)} | {rng} | {cont} |")
    L += ["", "## Score (direction correct / cases completed)", "", "| Approach | Correct | Completed | Not completed | Range contained real outcome |", "|---|---|---|---|---|"]
    for name, label in (("naive", "Naive (always flat)"), ("model_only", "Model only (one Nemotron call)"), ("vidhur", "Vidhur")):
        x = s[name]
        rc = f"{x['range_contained']}/{x['range_checked']}" if name != "naive" else "n/a"
        L.append(f"| {label} | {x['correct']} | {x['completed']} of {x['cases']} | {x['not_completed']} | {rc} |")
    L += ["", "## Detail per case", ""]
    for r in rows:
        v, m = r["vidhur"], r["model_only"]
        L.append(f"### {r['case_id']} - {r['theme']} ({r['as_of']})")
        L.append(f"- Real outcome: {r['outcome']['pct']:+.2f} % -> **{r['outcome']['direction']}**. Per-currency moves: "
                 + ", ".join(f"{c} {p:+.1f} %" for c, p in r["outcome"]["by_currency"].items()))
        L.append(f"- Model only: {m['status']}" + (f", predicted {m['pct_low']:+.1f} to {m['pct_high']:+.1f} % -> {m['direction']}. Reason: {m.get('reason', '')}"
                                                   if m["status"] == "completed" else f" ({m.get('error', '')})"))
        if v["status"] == "completed":
            L.append(f"- Vidhur: {v['n_verified']} verified claim(s), forecast {v['pct']:+.2f} % -> {v['direction']}; "
                     + ("; ".join(f"{c['analyst']} {c['part_id']} {c['direction']} (£{c['likely_gbp']:+,.0f}, {c['confidence']})" for c in v["verified"]) or "no claim passed the Evaluator, so it predicts flat"))
            L.append(f"- Vidhur work items: {v['delegations']}")
        else:
            L.append(f"- Vidhur: NOT COMPLETED - {v.get('error', '')}")
        L.append(f"- Traces: `{r['trace_vidhur']}` (Vidhur), `{r['trace_model_only']}` (model only)")
        L.append("")
    t = meta["llm"]
    L += ["## Honest limits", "",
          "- 5 cases and one run each: a single flipped case changes every percentage. Nothing here is statistically meaningful.",
          "- Company data is synthetic; the real outcome applies real exchange-rate moves to a synthetic purchase mix with 100 % pass-through, "
          "so it is a currency-driven proxy for landed cost, not Ganga's actual invoices. Freight, duties and part prices are not modelled.",
          "- Taiwan (the largest spend) is proxied by USD for the outcome, while Vidhur's Currency Analyst reports 'no data' for TWD.",
          "- News is a small set (2-7 real headlines per case, CNBC and Fortune only, dated from article URLs); with one publisher a claim "
          "cannot pass the two-independent-sources rule, so Vidhur then predicts flat.",
          "- The Decision Maker / Red Team stage was not run in the backtest (it does not alter the cost forecast).",
          f"- Nightshift 1.2.1 runtime: {t['ok']} successful model calls, {t['failed_attempts']} failed attempts "
          f"(HTTP status counts: {t['status_codes'] or 'none'}; routed models on failure: {', '.join(t['routed_models']) or '-'}; "
          f"models that answered: {', '.join(t['answered_models']) or '-'}). "
          f"Receipt ids: {', '.join(t['receipt_ids']) if t['receipt_ids'] else 'none exposed by the runtime'}."]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- network pieces
def historical_rate(day, currency, session):
    r = session.get(f"{FRANKFURTER}/{day}", params={"base": "GBP", "symbols": currency}, timeout=20)
    r.raise_for_status()
    return r.json()["rates"][currency]


def compute_outcome(targets, as_of, session):
    by_ccy = spend_by_currency(targets)
    end = as_of + timedelta(days=HORIZON_DAYS)
    start_rates = {c: historical_rate(as_of, c, session) for c in by_ccy}
    end_rates = {c: historical_rate(end, c, session) for c in by_ccy}
    pct = outcome_pct(by_ccy, start_rates, end_rates)
    total = sum(by_ccy.values())
    return {"pct": round(pct, 2), "direction": label_direction(pct), "gbp": round(pct / 100 * total, 2), "end_date": str(end),
            "by_currency": {c: round((start_rates[c] / end_rates[c] - 1) * 100, 2) for c in by_ccy}}


def main():
    import openai
    import requests
    from agents.currency_analyst import CurrencyAnalyst
    from agents.evaluator import Evaluator
    from agents.geopolitics_analyst import GeopoliticsAnalyst
    from agents.planner import Planner
    from agents.workflow import run_work_item
    from concurrent.futures import ThreadPoolExecutor
    from core import fx, llm
    from core.ganga_data import part_country_catalog, top_targets
    from core.shared_state import RunWorld
    from core.trace import Trace
    from core.zetaris_client import ZetarisClient, ZetarisError

    sys.stdout.reconfigure(encoding="utf-8")
    service_errors = (openai.OpenAIError, ZetarisError, requests.RequestException)
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    zt_real = ZetarisClient()
    if not zt_real.health_check():
        sys.exit("Zetaris health check failed.")
    session = requests.Session()
    real_fetch_fx_move = fx.fetch_fx_move
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated case ids to run, e.g. B2,B3")
    ap.add_argument("--previous", help="results jsonl of an interrupted run; its rows are kept as they are")
    args = ap.parse_args()
    rows = [json.loads(line) for line in open(args.previous, encoding="utf-8")] if args.previous else []
    results_path = Path(args.previous) if args.previous else config.ROOT / "logs" / f"backtest_results_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
    only = set(args.only.split(",")) if args.only else None

    for case in list(csv.DictReader(open(CASES_CSV, newline="", encoding="utf-8"))):
        if only and case["case_id"] not in only:
            continue
        as_of = date.fromisoformat(case["as_of"])
        zt = asof.AsOfZetaris(zt_real, as_of)
        targets = top_targets(zt, 99, max_quarter=zt.max_quarter)
        catalog = part_country_catalog(zt)
        by_part = {t.part_id: t for t in targets}
        total = sum(t.quarterly_spend_gbp for t in targets)
        headlines = asof.load_news(case["case_id"], as_of)
        print(f"\n=== {case['case_id']} as_of {as_of} | spend quarter {targets[0].quarter} | {len(headlines)} headlines ===")

        def news_stub(query, max_age_days=None, refresh=False, publisher_feeds=True, _h=headlines, _a=as_of):
            return list(asof.guard_articles(_h, _a)), {"fetched_at": f"{_a}T00:00:00+00:00", "from_cache": True, "errors": []}

        def fx_stub(currency, end=None, _a=as_of):
            end = end or _a
            asof.guard_fx_end(end, _a)
            move = real_fetch_fx_move(currency, end=end)
            asof.guard_fx_end(date.fromisoformat(move.end_date), _a)
            return move

        patches = [mock.patch("agents.geopolitics_analyst.fetch_news", news_stub), mock.patch("agents.currency_analyst.fetch_news", news_stub),
                   mock.patch("agents.planner.fetch_news", news_stub), mock.patch("core.fx.fetch_fx_move", fx_stub)]
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)

            # ---- (c) Vidhur
            trace = Trace(quiet=True)
            trace.path = trace.path.with_name(f"backtest_{case['case_id']}_vidhur_{trace.path.stem.removeprefix('run_')}.jsonl")
            world = RunWorld(f"bt-{case['case_id']}-{trace.run_id}")
            pool, stopped = {}, []
            analysts = {"geopolitics": GeopoliticsAnalyst(trace, world, pool), "currency": CurrencyAnalyst(trace, world, pool)}
            from core.calculator import CalcService  # GBP from the calculator, with the FX rate as at the case's as-of date
            calc = CalcService(zt, targets[0].quarter, as_of=as_of)
            evaluator = Evaluator(catalog, pool, world, trace, now=asof.as_of_datetime(as_of), calc_fn=calc.for_claim)
            planner = Planner(trace, world)
            world.start_run({"run_id": trace.run_id, "backtest_case": case["case_id"], "as_of": case["as_of"]})

            def guarded(stage, fn):
                try:
                    return fn()
                except service_errors as exc:
                    stopped.append({"stage": stage, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                    trace.event("backtest", f"stopped:{stage}", output_summary=stopped[-1]["error"], decision="STOPPED")

            def process(d):
                try:
                    run_work_item(d, by_part[d["part_id"]], analysts, evaluator, world, zt, trace, calc=calc)
                except service_errors as exc:
                    world.write_error(d["work_id"], f"{type(exc).__name__}: {str(exc)[:200]}")

            plan = guarded("planning", lambda: planner.plan(targets))
            if plan:
                with ThreadPoolExecutor(max_workers=2) as ex:
                    list(ex.map(process, plan["delegations"]))
                extra = guarded("review", lambda: planner.review(world.report(), targets))
                if extra:
                    process(extra)
            report = world.report()
            reason = incomplete_reason(report, stopped) or (None if plan else "no plan")
            leaked = [a for a in pool.values() if a.published[:10] > str(as_of) and a.feed != "frankfurter-api"]
            assert not leaked, "post-as_of article reached the pool"
            if reason:
                vid = {"status": "not_completed", "error": reason}
            else:
                agg = aggregate_vidhur(report, total)
                vid = {"status": "completed", **agg, "delegations": "; ".join(
                    f"{i['delegation']['work_id']} {i['delegation']['analyst']}/{i['delegation']['part_id']}={i['delegation'].get('status')}"
                    for i in report["items"] if i["delegation"].get("action") == "run")}
            vtrace = f"logs/{trace.path.name}"

            # ---- (b) model only: one call, same inputs, no Planner / Evaluator / Red Team
            mtrace = Trace(quiet=True)
            mtrace.path = mtrace.path.with_name(f"backtest_{case['case_id']}_modelonly_{mtrace.path.stem.removeprefix('run_')}.jsonl")
            fx_lines = []
            for cur in sorted({c for c in (fx.COUNTRY_CURRENCY.get(t.country) for t in targets) if c}):
                try:
                    mv = fx.fetch_fx_move(cur)
                    fx_lines.append(f"GBP vs {cur}: 30-day {mv.move_30d_pct:+.2f} %, 90-day {mv.move_90d_pct:+.2f} % (positive = {cur} dearer in pounds)")
                except Exception:
                    fx_lines.append(f"{cur}: no data")
            prompt = model_only_prompt(targets, fx_lines, headlines, as_of)
            try:
                res = llm.chat([{"role": "user", "content": prompt + " Keep your private reasoning brief."}], model=config.analyst_model(),
                               expected_model=config.analyst_expected(), agent="model_only", step="forecast", trace=mtrace, max_tokens=4000)
                data = llm.extract_json(res.text)
                parsed = parse_model_only(data)
                if parsed:
                    mid = (parsed[0] + parsed[1]) / 2
                    mod = {"status": "completed", "pct_low": parsed[0], "pct_high": parsed[1], "direction": label_direction(mid),
                           "reason": str(data.get("reason") or "")[:200],
                           "range_gbp": [round(parsed[0] / 100 * total, 2), round(parsed[1] / 100 * total, 2)]}
                else:
                    mod = {"status": "not_completed", "error": "reply had no usable change_pct_low / change_pct_high"}
            except service_errors as exc:
                mod = {"status": "not_completed", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            except ValueError:
                mod = {"status": "not_completed", "error": "reply could not be parsed as JSON"}
            mtrace_name = f"logs/{mtrace.path.name}"

        # ---- the real outcome is computed only now, after every approach has answered
        outcome = compute_outcome(targets, as_of, session)
        naive = {"status": "completed", "direction": "flat"}
        for a in (naive, mod, vid):
            if a["status"] == "completed":
                a["correct"] = a["direction"] == outcome["direction"]
        if mod["status"] == "completed":
            mod["contained"] = contains(mod["range_gbp"], outcome["gbp"], 1)
        if vid["status"] == "completed":
            vid["contained"] = contains(vid["range_gbp"], outcome["gbp"], vid["n_verified"])
        row = {"case_id": case["case_id"], "as_of": case["as_of"], "theme": case["theme"], "outcome": outcome, "naive": naive,
               "model_only": mod, "vidhur": vid, "trace_vidhur": vtrace, "trace_model_only": mtrace_name}
        rows.append(row)
        with open(results_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        print(f"outcome {outcome['direction']} ({outcome['pct']:+.2f}%) | naive flat | model-only {mod.get('direction', 'not completed')} "
              f"| vidhur {vid.get('direction', 'not completed')} ({vid.get('n_verified', '-')} verified)")

    llm_tot = {"ok": 0, "failed_attempts": 0, "status_codes": {}, "routed_models": set(), "answered_models": set(), "receipt_ids": []}
    rows.sort(key=lambda r: r["case_id"])
    for p in [config.ROOT / r[k] for r in rows for k in ("trace_vidhur", "trace_model_only")]:
        x = llm_summary(p)
        llm_tot["ok"] += x["ok"]
        llm_tot["failed_attempts"] += x["failed_attempts"]
        for k, v in x["status_codes"].items():
            llm_tot["status_codes"][k] = llm_tot["status_codes"].get(k, 0) + v
        llm_tot["routed_models"] |= set(x["routed_models"])
        llm_tot["answered_models"] |= set(x["answered_models"])
        llm_tot["receipt_ids"] += x["receipt_ids"]
    llm_tot["routed_models"], llm_tot["answered_models"] = sorted(llm_tot["routed_models"]), sorted(llm_tot["answered_models"])
    meta = {"started": started, "lane": config.analyst_model() + " (analysts, model-only) / " + config.evaluator_model() + " (Planner, Evaluator)",
            "retry": f"{os.environ.get('LLM_MAX_ATTEMPTS', '5')} attempts per call, {os.environ.get('LLM_RETRY_WAIT_SECONDS', '2')} s apart",
            "llm": llm_tot}
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(render_report(rows, meta), encoding="utf-8")
    print(f"\nReport: {REPORT}\n{json.dumps(score(rows), indent=1)}")


if __name__ == "__main__":
    main()
