"""Planted-claim test of the Evaluator's DETERMINISTIC checks only - no AI calls, no Nightshift, no Zetaris.

Runs the 25 original planted claims (data/eval/planted_claims.csv, unedited) plus 6 NEW planted claims (data/eval/planted_claims_new.csv,
one per new check type) through agents.evaluator.deterministic_checks and reports the results by failure type. The model check never runs,
so claims whose flaw is semantic (cause not supported, wrong date) are expected to be missed; they are reported as measured.

Data: Ganga's purchase rows come from data/purchases.csv (the same rows that are loaded into Zetaris), the GBP baseline from
core/calculator.py, FX rates live from the ECB via Frankfurter (a free data API). If Frankfurter is unreachable the FX-dependent cases
are reported NOT_EVALUATED, never as caught.

Usage:  python scripts/eval_planted_deterministic.py
Output: output_examples/eval_planted_deterministic.md and logs/eval_planted_det_<stamp>.jsonl
"""

import csv
import dataclasses
import importlib.util
import json
import os
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.chdir(Path(__file__).resolve().parents[1])

from core import config  # noqa: E402

DATA = config.ROOT / "data"
NEW_CLAIMS_CSV = DATA / "eval" / "planted_claims_new.csv"
NEW_SOURCES_CSV = DATA / "eval" / "planted_sources_new.csv"
REPORT = config.ROOT / "output_examples" / "eval_planted_deterministic.md"
OLD_RESULTS = config.ROOT / "logs" / "eval_planted_20261023_220200.jsonl"  # the AI-assisted run of 2026-10-23

# Fixed BEFORE the run: which failure type each planted case represents, and which check codes count as "the intended check".
FAILURE_TYPES = ["fabricated number", "FX mismatch", "wrong currency", "GBP mismatch", "duplicate source", "stale source", "other"]
TYPE_OF_CASE = {"F03": "fabricated number", "N01": "fabricated number",
                "F01": "FX mismatch", "F02": "FX mismatch", "N02": "FX mismatch",
                "F04": "wrong currency", "N03": "wrong currency",
                "F15": "GBP mismatch", "F16": "GBP mismatch", "N04": "GBP mismatch",
                "F11": "duplicate source", "F12": "duplicate source", "N05": "duplicate source",
                "F05": "stale source", "F06": "stale source", "N06": "stale source"}
INTENDED_CODES = {"fabricated number": {"NUMBER_NOT_IN_SOURCE", "FX_MISMATCH"}, "FX mismatch": {"FX_MISMATCH"},
                  "wrong currency": {"CURRENCY_COUNTRY_MISMATCH"}, "GBP mismatch": {"GBP_MISMATCH"},
                  "duplicate source": {"DUPLICATE_SOURCE"}, "stale source": {"STALE_SOURCE"}, "other": set()}
RENAMED = {"NOT_INDEPENDENT": "DUPLICATE_SOURCE"}  # the old code for copied reports; the CSV is left as it was


def failure_type(case_id):
    return TYPE_OF_CASE.get(case_id, "other")


def load_planted():
    spec = importlib.util.spec_from_file_location("eval_planted_claims", config.ROOT / "scripts" / "eval_planted_claims.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def summarise(results):
    """{failure type: {planted, caught, caught_for_intended_check, missed, not_evaluated}} over the false claims."""
    out = OrderedDict((t, {"planted": 0, "caught": 0, "intended": 0, "missed": 0, "not_evaluated": 0}) for t in FAILURE_TYPES)
    for r in results:
        if r["group"] != "false":
            continue
        row = out[failure_type(r["case_id"])]
        row["planted"] += 1
        if r["outcome"] == "CAUGHT":
            row["caught"] += 1
            row["intended"] += bool(INTENDED_CODES[failure_type(r["case_id"])] & set(r["reason_codes"]))
        elif r["outcome"] == "MISSED":
            row["missed"] += 1
        else:
            row["not_evaluated"] += 1
    return out


def old_outcomes():
    """{case_id: outcome} from the saved AI-assisted run, or {} if the file is not there."""
    if not OLD_RESULTS.exists():
        return {}
    return {r["case_id"]: r for r in map(json.loads, OLD_RESULTS.read_text(encoding="utf-8").splitlines())}


def old_summary(old, original_ids):
    out = OrderedDict((t, {"planted": 0, "caught": 0, "missed": 0, "not_evaluated": 0}) for t in FAILURE_TYPES)
    for cid, r in old.items():
        if r["group"] != "false" or cid not in original_ids:
            continue
        row = out[failure_type(cid)]
        row["planted"] += 1
        row[{"CAUGHT": "caught", "MISSED": "missed"}.get(r["outcome"], "not_evaluated")] += 1
    return out


def render_report(results, old, meta):
    new_rows = summarise([r for r in results if not r["case_id"].startswith("N")])
    new_cases = [r for r in results if r["case_id"].startswith("N")]
    new_sum = summarise(new_cases)
    old_sum = old_summary(old, {r["case_id"] for r in results if not r["case_id"].startswith("N")})
    controls = [r for r in results if r["group"] == "control"]
    n_false = sum(r["group"] == "false" and not r["case_id"].startswith("N") for r in results)
    total_caught = sum(v["caught"] for v in new_rows.values())
    old_caught = sum(v["caught"] for v in old_sum.values())
    L = ["# Planted-claim test: deterministic checks only (no AI)", "",
         "Vidhur's Evaluator (professional scepticism) runs exact, rule-based checks before it asks a model anything. This run uses ONLY those "
         "checks: no Nightshift, no model call. The 25 original planted claims (`data/eval/planted_claims.csv`) are unedited; 6 NEW claims "
         "(`data/eval/planted_claims_new.csv`, one per new check) are reported separately.", "",
         f"- Run: {meta['started']} | results: `{meta['results_file']}` | purchase rows: `data/purchases.csv` ({meta['quarter']}) | "
         f"USD/GBP for the baseline: {meta['fx']}",
         "- Articles are prepared evaluation data (`data/eval/planted_sources*.csv`, synthetic - not real news). ECB rates are live (Frankfurter).",
         "- Small sample written by the builder: it shows which flaws each check catches, not a general accuracy figure.", "",
         "## Result", "",
         f"- **Original 25 claims, deterministic checks only: {total_caught}/{n_false} false claims caught** "
         f"(the AI-assisted run of 2026-10-23 caught {old_caught}/{n_false} of the same 20 - see the table; that run also used the model check).",
         f"- **Correct control claims wrongly rejected: {sum(r['outcome'] == 'WRONGLY_REJECTED' for r in controls)}/{len(controls)}** "
         f"(correctly passed: {sum(r['outcome'] == 'CORRECTLY_PASSED' for r in controls)}; not evaluated: {sum(r['outcome'] == 'NOT_EVALUATED' for r in controls)}).",
         f"- **6 NEW claims: {sum(v['caught'] for v in new_sum.values())}/{len(new_cases)} caught** "
         f"({sum(v['intended'] for v in new_sum.values())} by their intended check).", "",
         "## Original 20 false claims, by failure type (old = AI-assisted run, new = deterministic checks only)", "",
         "| Failure type | Planted | Old: caught | Old: missed | Old: not evaluated | New: caught | New: caught by the intended check | New: missed | New: not evaluated |",
         "|---|---|---|---|---|---|---|---|---|"]
    for t in FAILURE_TYPES:
        n, o = new_rows[t], old_sum[t]
        L.append(f"| {t} | {n['planted']} | {o['caught']} | {o['missed']} | {o['not_evaluated']} | {n['caught']} | {n['intended']} | {n['missed']} | {n['not_evaluated']} |")
    L += ["", "\"Caught by the intended check\" = the send-back reasons include the check built for that failure type "
          "(fabricated number: NUMBER_NOT_IN_SOURCE or FX_MISMATCH; FX: FX_MISMATCH; wrong currency: CURRENCY_COUNTRY_MISMATCH; GBP: GBP_MISMATCH; "
          "duplicate: DUPLICATE_SOURCE; stale: STALE_SOURCE). A claim can be caught by an unrelated check and still count as caught. "
          "'other' (cause not supported, wrong date, untrusted publisher, single source, wrong country) has no dedicated number check.", "",
          "## The 6 NEW planted claims (one per new check)", "",
          "| Case | Failure type | Planted flaw | Outcome | Send-back reason code(s) |", "|---|---|---|---|---|"]
    for r in new_cases:
        L.append(f"| {r['case_id']} (NEW) | {failure_type(r['case_id'])} | {r['what_is_wrong'].replace('|', '/')} | {r['outcome']} | {', '.join(r['reason_codes']) or '-'} |")
    L += ["", "## Every original case", "", "| Case | Failure type | Planted flaw | Old (AI-assisted) | New (deterministic) | New: reason code(s) |", "|---|---|---|---|---|---|"]
    for r in results:
        if r["case_id"].startswith("N"):
            continue
        L.append(f"| {r['case_id']} | {failure_type(r['case_id']) if r['group'] == 'false' else 'control'} | {r['what_is_wrong'].replace('|', '/')} | "
                 f"{old.get(r['case_id'], {}).get('outcome', 'n/a')} | {r['outcome']} | {', '.join(r['reason_codes']) or '-'} |")
    L += ["", "## Notes and limits", "",
          "- The test cases were not edited to improve results. The only harness change: the old code `NOT_INDEPENDENT` (copied reports) was renamed "
          "`DUPLICATE_SOURCE`, and the expected code in the unedited CSV is mapped to it.",
          "- Claims whose flaw is about meaning (F17 prices fell, F19/F20 wrong dates) need the model check; with no AI they are missed. That is the "
          "price of running offline and is why the model check still exists.",
          "- Controls C01-C04 carry an analyst percentage range with no source label (the old format); the number check only inspects figures written "
          "in the claim text and ranges explicitly labelled FORECAST.",
          "- FX checks use live ECB data: re-running on another day uses that day's rates (the planted flaw is relative to the real rate, so results hold)."]
    return "\n".join(L) + "\n"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    import requests
    from agents.evaluator import deterministic_checks
    from core import calculator, fx
    from core.claims import Verdict

    planted = load_planted()
    now = datetime.now(timezone.utc)
    rows = planted.read_csv(planted.CLAIMS_CSV) + planted.read_csv(NEW_CLAIMS_CSV)
    pool = planted.build_pool(planted.read_csv(planted.SOURCES_CSV) + planted.read_csv(NEW_SOURCES_CSV), now)

    parts = {r["part_id"]: r for r in csv.DictReader(open(DATA / "parts.csv", newline="", encoding="utf-8"))}
    suppliers = {r["supplier_id"]: r for r in csv.DictReader(open(DATA / "suppliers.csv", newline="", encoding="utf-8"))}
    catalog = {pid: {"part_name": p["part_name"], "country": suppliers[p["supplier_id"]]["country"]} for pid, p in parts.items()}
    purchases = list(csv.DictReader(open(DATA / "purchases.csv", newline="", encoding="utf-8")))
    quarter = max(r["quarter"] for r in purchases)
    rows_by_part = {}
    for r in purchases:
        if r["quarter"] == quarter:
            rows_by_part.setdefault(r["part_id"], []).append(calculator.PurchaseRow(
                r["part_id"], parts[r["part_id"]]["part_name"], catalog[r["part_id"]]["country"], float(r["quantity"]), float(r["unit_price"]),
                r["currency"], quarter, float(r["total_gbp"]), float(r["gbp_per_currency_unit"])))
    assumptions = calculator.load_assumptions()
    try:
        usd_rate, usd_day = fx.rate_on("USD")
        fx_note = f"1 GBP = {usd_rate} USD on {usd_day} (ECB)"
    except Exception as exc:  # no network: the stored row rate is used for the baseline
        usd_rate, usd_day = 1 / rows_by_part["P001"][0].row_rate_gbp_per_unit, quarter
        fx_note = f"row rate (live FX unavailable: {type(exc).__name__})"

    fx_moves, fx_articles = {}, {}
    for cur in sorted({r["fx_currency"] for r in rows if r["fx_currency"]}):
        try:
            fx_moves[cur] = fx.fetch_fx_move(cur)
            fx_articles[cur] = fx.fx_source_article(fx_moves[cur])
            pool[fx_articles[cur].url] = fx_articles[cur]
        except Exception as exc:
            print(f"FX data for {cur} unavailable ({type(exc).__name__}); its cases will be NOT_EVALUATED")

    def units_per_gbp(rs):  # live USD rate for USD rows; the stored row rate for any other currency (e.g. P006 in EUR)
        return usd_rate if rs[0].currency == "USD" else 1 / rs[0].row_rate_gbp_per_unit

    baselines = {pid: calculator.compute(rs, _stub_claim(pid), units_per_gbp(rs), usd_day, "ECB", assumptions).baseline_quarterly_spend_gbp
                 for pid, rs in rows_by_part.items()}
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = config.ROOT / "logs" / f"eval_planted_det_{stamp}.jsonl"
    results = []
    for row in rows:
        rec_base = {"case_id": row["case_id"], "group": row["group"], "category": row["category"], "what_is_wrong": row["what_is_wrong"],
                    "expected_codes": ";".join(RENAMED.get(c, c) for c in row["expected_codes"].split(";") if c)}
        if row["fx_currency"] and row["fx_currency"] not in fx_moves:
            results.append({**rec_base, "outcome": "NOT_EVALUATED", "reason_codes": [], "detail": "ECB data unavailable"})
            continue
        claim = planted.build_claim(row, pool, fx_articles, fx_moves, baselines, names={p: c["part_name"] for p, c in catalog.items()})
        if row.get("fx_mode") == "bad_rate":
            claim.computed["end_rate"] = round(float(claim.computed["end_rate"]) * 1.08, 4)  # 8% away from the real rate
        # the planted claims are written for each supplier country's own currency; price the stub rows in the claim's currency so only
        # the planted fault is under test (the FX-pair check has its own unit test)
        stub_rows = [dataclasses.replace(r, currency=claim.currency or r.currency) for r in rows_by_part[claim.part_id]]
        calc = calculator.compute(stub_rows, claim, units_per_gbp(rows_by_part[claim.part_id]), usd_day, "ECB", assumptions).calc
        findings = deterministic_checks(claim, pool, catalog, now=now, calc=calc, fx_rate_fn=fx.rate_on)
        verdict = Verdict("SEND_BACK", [f"{f.code}: {f.message}" for f in findings]) if findings else Verdict("PASS", ["deterministic checks passed"])
        rec = planted.classify({**row, "expected_codes": rec_base["expected_codes"]}, verdict)
        results.append(rec)
        with open(results_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"{row['case_id']} {row['group']:<7} {rec['outcome']:<17} {', '.join(rec['reason_codes']) or '-'}")

    meta = {"started": now.isoformat(timespec="seconds"), "results_file": f"logs/{results_file.name}", "quarter": quarter, "fx": fx_note}
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(render_report(results, old_outcomes(), meta), encoding="utf-8")
    print(f"\nReport: {REPORT}")


def _stub_claim(part_id):
    from core.claims import Claim
    return Claim("x", "", part_id, "", "", "cost_up", 0.0, 0.0, [])


if __name__ == "__main__":
    main()
