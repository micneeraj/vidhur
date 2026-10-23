"""Planted false-claim test of the Evaluator (Vidhur's "professional scepticism" layer).

Sends 20 deliberately false or weak claims and 5 correct control claims (data/eval/planted_claims.csv) through the SAME
Evaluator the product uses: real Zetaris queries (part/country catalog, GBP spend), the real deterministic checks and
the real Nightshift model check. Nothing is mocked and nothing is cached.

Scoring rules (fixed before the run):
  caught                a false claim the Evaluator sent back (for any reason)
  missed                a false claim the Evaluator passed
  wrongly rejected      a control claim the Evaluator sent back
  could not be evaluated  a runtime error (Nightshift 5xx/timeout after retries, Zetaris, unparseable model reply). Counted
                        separately and NEVER as caught or as correct.

The articles are prepared evaluation data (data/eval/planted_sources.csv, synthetic - not real news); the ECB exchange-rate
data used for the currency claims is fetched live from Frankfurter.

Usage:  python scripts/eval_planted_claims.py
Retry settings for a busy runtime: LLM_MAX_ATTEMPTS=4 LLM_RETRY_WAIT_SECONDS=60 (one try + 3 retries, 60 s apart).
Output: output_examples/eval_planted_claims.md and logs/eval_planted_<stamp>.jsonl (+ the usual trace in logs/run_*.jsonl).
"""

import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.chdir(Path(__file__).resolve().parents[1])

from core import config  # noqa: E402
from core.evalutil import llm_facts_for, llm_summary  # noqa: E402,F401

CLAIMS_CSV = config.ROOT / "data" / "eval" / "planted_claims.csv"
SOURCES_CSV = config.ROOT / "data" / "eval" / "planted_sources.csv"
REPORT = config.ROOT / "output_examples" / "eval_planted_claims.md"
UNPARSEABLE_MARK = "reply could not be parsed"


# --------------------------------------------------------------------------- pure helpers (unit tested, no network)
def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_pool(source_rows, now):
    """{url: Article} from the prepared evaluation articles. Hosts end in .example, so the publisher name decides trust."""
    from core.news import Article
    pool = {}
    for r in source_rows:
        url = f"https://eval.example/{r['source_id']}"
        published = (now - timedelta(days=int(r["days_old"]))).isoformat(timespec="seconds")
        pool[url] = Article(title=r["title"], publisher=r["publisher"], url=url, published=published,
                            summary=r["summary"], feed="prepared-evaluation-data")
    return pool


def fill_gbp(text, spend_by_part):
    """{gbp:P001:10} -> the pound amount (Ganga's real quarterly spend on P001 from Zetaris, times 10)."""
    def repl(m):
        return f"GBP {spend_by_part[m.group(1)] * float(m.group(2)):,.0f}"
    return re.sub(r"\{gbp:(P\d+):([\d.]+)\}", repl, text)


def apply_fx_mode(mode, move):
    """(direction, pct_low, pct_high, computed) for a currency claim built from a real FXMove, planted per `mode`."""
    m30, m90 = move.move_30d_pct, move.move_90d_pct
    computed = {"currency": move.currency, "move_30d_pct": m30, "move_90d_pct": m90, "start_date": move.start_date,
                "end_date": move.end_date, "start_rate": move.start_rate, "end_rate": move.end_rate,
                "pass_through": "100% (upper bound)"}
    direction = "cost_up" if m90 > 0 else "cost_down"
    low, high = sorted((round(abs(m30), 2), round(abs(m90), 2)))
    if mode == "bad_range":
        low, high = round(low * 3, 2), round(high * 3 + 1, 2)
    elif mode == "bad_direction":
        direction = "cost_down" if direction == "cost_up" else "cost_up"
    elif mode == "falsified":
        computed = {**computed, "move_30d_pct": 6.0, "move_90d_pct": 9.0}  # not what the ECB data says
        direction, low, high = "cost_up", 6.0, 9.0
    return direction, low, high, computed


def build_claim(row, pool, fx_articles, fx_moves, spend_by_part, names=None):
    """A core.claims.Claim for one CSV row. fx_articles/fx_moves are keyed by currency code."""
    from core.claims import Claim
    urls, computed = [], {}
    direction, low, high = row["direction"], float(row["pct_low"]), float(row["pct_high"])
    for sid in row["sources"].split(";"):
        urls.append(fx_articles[row["fx_currency"]].url if sid == "FX" else f"https://eval.example/{sid}")
    if row["analyst"] == "currency":
        direction, low, high, computed = apply_fx_mode(row["fx_mode"], fx_moves[row["fx_currency"]])
    return Claim(claim_id=row["case_id"], claim=fill_gbp(row["claim"], spend_by_part), part_id=row["part_id"],
                 part_name=(names or {}).get(row["part_id"], row["part_id"]), supplier_country=row["supplier_country"], direction=direction,
                 pct_low=low, pct_high=high, source_urls=urls, reasoning=row["reasoning"], analyst=row["analyst"],
                 computed=computed)


def reason_codes(reasons):
    """'COUNTRY_MISMATCH: ...' -> 'COUNTRY_MISMATCH'."""
    return [r.split(":", 1)[0].strip() for r in reasons]


def classify(row, verdict, error=None):
    """One result record. `verdict` is a core.claims.Verdict or None; `error` the runtime error text."""
    is_false = row["group"] == "false"
    rec = {"case_id": row["case_id"], "group": row["group"], "category": row["category"],
           "what_is_wrong": row["what_is_wrong"], "expected_codes": row["expected_codes"]}
    if error or verdict is None:
        return {**rec, "outcome": "NOT_EVALUATED", "reason_codes": [], "detail": error or "no verdict"}
    if verdict.verdict == "SEND_BACK" and any(UNPARSEABLE_MARK in r for r in verdict.reasons):
        return {**rec, "outcome": "NOT_EVALUATED", "reason_codes": [], "detail": "model reply unparseable: " + " | ".join(verdict.reasons)}
    sent_back = verdict.verdict == "SEND_BACK"
    codes = reason_codes(verdict.reasons) if sent_back else []  # a pass carries the model's explanation, not a reason code
    if is_false:
        outcome = "CAUGHT" if sent_back else "MISSED"
    else:
        outcome = "WRONGLY_REJECTED" if sent_back else "CORRECTLY_PASSED"
    expected = [c for c in row["expected_codes"].split(";") if c]
    return {**rec, "outcome": outcome, "reason_codes": codes, "detail": " | ".join(verdict.reasons)[:300],
            "llm_checked": verdict.llm_checked,
            "right_reason": (any(c in codes for c in expected) if expected and sent_back else None)}


def score(results):
    false = [r for r in results if r["group"] == "false"]
    ctrl = [r for r in results if r["group"] == "control"]
    count = lambda rs, o: sum(r["outcome"] == o for r in rs)  # noqa: E731
    return {
        "false_total": len(false), "caught": count(false, "CAUGHT"), "missed": count(false, "MISSED"),
        "false_not_evaluated": count(false, "NOT_EVALUATED"),
        "caught_for_expected_reason": sum(1 for r in false if r["outcome"] == "CAUGHT" and r.get("right_reason")),
        "control_total": len(ctrl), "wrongly_rejected": count(ctrl, "WRONGLY_REJECTED"),
        "correctly_passed": count(ctrl, "CORRECTLY_PASSED"), "control_not_evaluated": count(ctrl, "NOT_EVALUATED"),
    }


def by_category(results):
    cats = {}
    for r in results:
        if r["group"] != "false":
            continue
        c = cats.setdefault(r["category"], {"n": 0, "caught": 0, "missed": 0, "not_evaluated": 0})
        c["n"] += 1
        c[{"CAUGHT": "caught", "MISSED": "missed", "NOT_EVALUATED": "not_evaluated"}[r["outcome"]]] += 1
    return cats


def render_report(results, meta):
    s = score(results)
    evaluated_false = s["false_total"] - s["false_not_evaluated"]
    lines = [
        "# Planted false-claim test of the Evaluator", "",
        "Vidhur's Evaluator is its professional-scepticism layer: the auditor's way of not accepting a claim without evidence. "
        "This test plants 20 false or weak claims (and 5 correct controls) and checks whether the Evaluator sends them back.", "",
        f"- Run: {meta['started']} | trace: `{meta['trace']}` | results: `{meta['results_file']}`",
        f"- Evaluator model lane: `{meta['lane']}` | retry policy: {meta['retry']}",
        "- Claims: `data/eval/planted_claims.csv`. Articles: `data/eval/planted_sources.csv` - **prepared evaluation data "
        "(synthetic articles, not real news)**. Ganga's data comes from Zetaris (synthetic company); the ECB exchange rates are live.",
        "- One run, 25 claims, a small sample. Same Evaluator code path as the product; no mocks, no response caching.", "",
        "## Result", "",
        f"- **False claims caught: {s['caught']}/{s['false_total']}** (missed: {s['missed']}; could not be evaluated: {s['false_not_evaluated']})",
        f"- **Correct claims wrongly rejected: {s['wrongly_rejected']}/{s['control_total']}** (correctly passed: {s['correctly_passed']}; could not be evaluated: {s['control_not_evaluated']})",
        f"- **Could not be evaluated because of runtime errors: {s['false_not_evaluated'] + s['control_not_evaluated']}** "
        "(counted separately, never as caught)",
        f"- Of the {s['caught']} caught, {s['caught_for_expected_reason']} were caught for the planted reason code "
        "(claims without an expected code in the CSV are not counted there).",
    ]
    if s["false_not_evaluated"]:
        lines.append(f"- Among the claims that could be evaluated, {s['caught']}/{evaluated_false} false claims were caught.")
    lines += ["", "## By category (false claims)", "", "| Category | Planted | Caught | Missed | Not evaluated |", "|---|---|---|---|---|"]
    for cat, c in by_category(results).items():
        lines.append(f"| {cat} | {c['n']} | {c['caught']} | {c['missed']} | {c['not_evaluated']} |")
    lines += ["", "## Every case", "",
              "| Case | Planted flaw | Outcome | Send-back reason code(s) | Model that answered | Routed model / failure detail (on a failed call) | Receipt id |",
              "|---|---|---|---|---|---|---|"]
    for r in results:
        flaw = r["what_is_wrong"].replace("|", "/")
        lines.append(f"| {r['case_id']} | {flaw} | {r['outcome']} | {', '.join(r['reason_codes']) or '-'} | "
                     f"{r.get('answered_model') or '-'} | {r.get('routed_model') or '-'} | {r.get('receipt_id') or 'not exposed by Nightshift 1.2.1'} |")
    lines += ["", "## Notes and limits", "",
              "- The deterministic checks (part and country against Zetaris, source count, freshness, independence, trust tier, FX numbers against the "
              "attached ECB moves) run first; only claims that pass them reach the Nemotron support check.",
              "- Outcomes marked MISSED are reported as measured. In particular the Evaluator does not compare a GBP figure written inside a claim "
              "with Ganga's purchase data (the GBP impact is computed from Zetaris in Python after the claim passes), and it trusts the "
              "FX moves attached to a currency claim rather than re-fetching them. Those are gaps in this version, not test errors.",
              "- Nightshift 1.2.1 often returns 502; a failed model check is NOT_EVALUATED, never a catch. Each call retries 3 times, 60 s apart.",
              f"- Model-call failures during this run: {meta['llm_fail']} failed attempts, {meta['llm_ok']} successful calls.",
              "- Sample size is 25 claims written by the builder; this shows what the Evaluator does and does not catch, not a general accuracy figure."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- the run
def rerender(results_file):
    """Rebuild the report from a saved results file (no model calls). Used after a reporting fix."""
    results_file = Path(results_file)
    results = [json.loads(line) for line in results_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    for r in results:  # records saved before the fix may carry a model explanation in reason_codes
        if r["outcome"] not in ("CAUGHT", "WRONGLY_REJECTED"):
            r["reason_codes"] = []
    stamp = results_file.stem.removeprefix("eval_planted_")
    trace = f"logs/run_{stamp}.jsonl"
    summ = llm_summary(config.ROOT / trace)
    meta = {"started": datetime.strptime(stamp, "%Y%m%d_%H%M%S").isoformat(timespec="seconds") + " (local time)", "trace": trace,
            "results_file": f"logs/{results_file.name}", "lane": config.evaluator_model(),
            "retry": "4 attempts per call, 60 s apart", "llm_fail": summ["failed_attempts"], "llm_ok": summ["ok"]}
    REPORT.write_text(render_report(results, meta), encoding="utf-8")
    print(f"Report re-rendered from {results_file.name}: {REPORT}")


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--render":
        return rerender(sys.argv[2])
    import openai
    import requests
    from agents.evaluator import Evaluator
    from core import fx
    from core.ganga_data import part_country_catalog, quarterly_spend
    from core.shared_state import RunWorld
    from core.trace import Trace
    from core.zetaris_client import ZetarisClient, ZetarisError

    sys.stdout.reconfigure(encoding="utf-8")
    now = datetime.now(timezone.utc)
    rows, source_rows = read_csv(CLAIMS_CSV), read_csv(SOURCES_CSV)
    trace = Trace()
    world = RunWorld("eval-" + trace.run_id)
    stamp = trace.path.stem.removeprefix("run_")
    results_file = config.ROOT / "logs" / f"eval_planted_{stamp}.jsonl"

    zt = ZetarisClient(trace=trace)
    if not zt.health_check():
        sys.exit("Zetaris health check failed.")
    catalog = part_country_catalog(zt)
    spend_by_part = {p: quarterly_spend(zt, p)[0] for p in {r["part_id"] for r in rows}}

    pool = build_pool(source_rows, now)
    fx_moves, fx_articles = {}, {}
    for cur in sorted({r["fx_currency"] for r in rows if r["fx_currency"]}):
        fx_moves[cur] = fx.fetch_fx_move(cur)  # live ECB data via Frankfurter
        fx_articles[cur] = fx.fx_source_article(fx_moves[cur])
        pool[fx_articles[cur].url] = fx_articles[cur]

    evaluator = Evaluator(catalog, pool, world, trace, now=now)
    print(f"Planted-claim test | {len(rows)} claims | trace: {trace.path} | lane: {config.evaluator_model()}")
    results = []
    for row in rows:
        claim = build_claim(row, pool, fx_articles, fx_moves, spend_by_part, names={p: c["part_name"] for p, c in catalog.items()})
        key = world.write_claim(claim, row["case_id"], "eval_planted_claims")
        verdict, error = None, None
        with trace.target(row["case_id"]):
            try:
                verdict = evaluator.evaluate(key)
            except (openai.OpenAIError, ZetarisError, requests.RequestException) as exc:
                error = f"{type(exc).__name__}: {str(exc)[:200]}"
                trace.event("evaluator", "error", input_summary=row["case_id"], output_summary=error, decision="NOT_EVALUATED")
        rec = classify(row, verdict, error)
        rec.update(llm_facts_for(trace.path, row["case_id"]))
        results.append(rec)
        with open(results_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"{row['case_id']} {row['group']:<7} {rec['outcome']:<17} {', '.join(rec['reason_codes']) or rec['detail'][:80]}")

    meta = {"started": now.isoformat(timespec="seconds"), "trace": f"logs/{trace.path.name}",
            "results_file": f"logs/{results_file.name}", "lane": config.evaluator_model(),
            "retry": f"{os.environ.get('LLM_MAX_ATTEMPTS', '5')} attempts per call, {os.environ.get('LLM_RETRY_WAIT_SECONDS', '2')} s apart",
            "llm_fail": trace.stats["llm_fail"], "llm_ok": trace.stats["llm_ok"]}
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(render_report(results, meta), encoding="utf-8")
    s = score(results)
    print(f"\nFalse claims caught {s['caught']}/{s['false_total']} | correct claims wrongly rejected {s['wrongly_rejected']}/{s['control_total']} "
          f"| not evaluated {s['false_not_evaluated'] + s['control_not_evaluated']}")
    print(f"Report: {REPORT}")


if __name__ == "__main__":
    main()
