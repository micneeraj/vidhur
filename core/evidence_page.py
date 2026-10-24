"""The evidence trail page: one self-contained HTML file (no scripts, no external services), built ONLY from the world-state report.

Top: the CFO brief summary (drivers, worst / likely / best, confidence, recommendation).
Then per driver: claim -> sources -> deterministic checks -> Evaluator verdicts and send-backs -> Red Team challenge and reason
-> final decision -> GBP calculation record. Drivers that could not be verified are shown as such, never as zero.
"""

from html import escape

from core import calculator
from core import decisions as dec
from core import next_steps
from core import unverified
from core.source_trust import trust_of

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c2430;--mute:#5d6877;--line:#dfe3ea;--ok:#127a43;--okbg:#e4f5ec;--bad:#b3261e;--badbg:#fdeceb;
--warn:#8a5a00;--warnbg:#fff3d6;--blue:#1f4fb4;--bluebg:#e8effd}
@media (prefers-color-scheme:dark){:root{--bg:#12161c;--card:#1b212a;--ink:#e8ecf2;--mute:#9aa6b6;--line:#2c3542;--ok:#5fd394;--okbg:#14301f;
--bad:#ff8a80;--badbg:#3a1a18;--warn:#f2c36b;--warnbg:#3a2e12;--blue:#8fb2ff;--bluebg:#18264a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
main{max-width:1040px;margin:0 auto;padding:20px 16px 60px}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:28px 0 10px}
h3{font-size:15px;margin:0 0 6px}.sub{color:var(--mute);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mute);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}
.pill{display:inline-block;padding:1px 9px;border-radius:99px;font-size:12px;font-weight:600}
.ok{background:var(--okbg);color:var(--ok)}.bad{background:var(--badbg);color:var(--bad)}.warn{background:var(--warnbg);color:var(--warn)}.info{background:var(--bluebg);color:var(--blue)}
.num{font-variant-numeric:tabular-nums;white-space:nowrap}.step{border-left:3px solid var(--line);padding:2px 0 2px 12px;margin:12px 0}
.step b.t{display:block;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mute)}
a{color:var(--blue);word-break:break-all}.banner{background:var(--warnbg);color:var(--warn);border-radius:8px;padding:8px 12px;margin:10px 0;font-size:14px}
details summary{cursor:pointer;color:var(--mute);font-size:13px}code{font-size:13px}
@media (max-width:640px){table{font-size:13px}.num{white-space:normal}}
"""

NOTE = ("Every number is labelled: OBSERVED = read from data (Zetaris purchase rows, ECB rates); FORECAST = stated by a cited source; "
        "SCENARIO = our assumption (data/decision_assumptions.csv). All GBP figures are calculated in Python, never by the AI.")


def g(v):
    return f"{'+' if v > 0 else '-' if v < 0 else ''}£{abs(v):,.0f}"


def pill(text, kind):
    return f'<span class="pill {kind}">{escape(str(text))}</span>'


def status_pill(status):
    return {"PASS": pill("Verified", "ok"), "UNVERIFIED": pill(unverified.LABEL, "warn"), "NOT_ASSESSED": pill(unverified.LABEL, "warn"),
            "NO_CLAIM": pill("No claim", "info")}.get(status, pill(status, "info"))


def _sources_html(world, claim):
    rows = []
    for url in dict.fromkeys(claim.source_urls):
        art = world.article(url)
        if art:
            _, tier, score = trust_of(url, art["publisher"])
            rows.append(f'<tr><td>{escape(art["publisher"])}</td><td>Tier {tier} (trust {score:g})</td><td class="num">{escape(art["published"][:10])}</td>'
                        f'<td>{escape(art["title"])}<br><a href="{escape(url)}" target="_blank" rel="noopener">link</a></td></tr>')
        else:
            rows.append(f'<tr><td colspan="3">{pill("not in the fetched evidence", "bad")}</td><td>{escape(url[:120])}</td></tr>')
    return ('<table><tr><th>Publisher</th><th>Trust</th><th>Date</th><th>Headline</th></tr>' + "".join(rows) + "</table>") if rows else "<p>No sources.</p>"


def _checks_html(verdict):
    checks = verdict.get("checks") or []
    if not checks:
        return '<p class="sub">No deterministic check record for this attempt.</p>'
    return "<table>" + "".join(
        f'<tr><td style="width:60px">{pill("pass", "ok") if c["ok"] else pill("fail", "bad")}</td><td>{escape(c["name"])}'
        f'{"<br><span class=sub>" + escape(c["detail"]) + "</span>" if c["detail"] else ""}</td></tr>' for c in checks) + "</table>"


def _attempts_html(item, world):
    out = []
    for a in item["attempts"]:
        c = a["claim"]
        out.append(f'<div class="step"><b class="t">Attempt {c.attempt} - claim</b>{escape(c.claim)}'
                   f'<div class="sub">{escape(c.direction.replace("_", " "))} {c.pct_low:g}-{c.pct_high:g}% ({escape(c.pct_label or "unlabelled")}) - '
                   f'{len(set(c.source_urls))} source(s) cited</div>')
        out.append("<details open><summary>Sources</summary>" + _sources_html(world, c) + "</details>")
        for v in a["verdicts"]:
            ok = v["verdict"] == "PASS"
            how = f", model check: {v['strength']}" if v.get("llm_checked") else ", model check skipped because a deterministic check failed"
            out.append("<details open><summary>Deterministic checks</summary>" + _checks_html(v) + "</details>")
            out.append(f'<p><b>Evaluator:</b> {pill("PASS" if ok else "SENT BACK", "ok" if ok else "bad")} '
                       f'<span class="sub">({v["independent_sources"]} independent source(s){escape(how)})</span><br>'
                       + "<br>".join(escape(r) for r in v["reasons"]) + "</p>")
        out.append("</div>")
    return "".join(out)


def calc_rows(item):
    """[(label, html)] of the GBP calculation record (inputs, FX rate, baseline, move, formula, result, assumptions); [] if there is none."""
    calc = (item["impact"] or {}).get("calc") or {}
    if not calc:
        return []
    fx, m, res = calc["fx"], calc["move_pct"], calc["result_gbp"]
    inputs = "<br>".join(f'{escape(r["part_id"])} {escape(r["part_name"])}, {escape(r["quarter"])}: {r["quantity"]:,.0f} x {r["unit_price"]:,.2f} {escape(r["currency"])}'
                         for r in calc["inputs"])
    return [("Input rows (OBSERVED, Zetaris)", inputs),
            ("FX rate (OBSERVED)", f'1 GBP = {fx["units_per_gbp"]} {escape(fx["currency"])} on {escape(fx["date"])} ({escape(fx["source"])})'),
            ("Baseline (OBSERVED)", f'£{calc["baseline_gbp"]:,.0f} per quarter'),
            (f'Move ({escape(m["label"])})', f'{m["small"]:g}% / {m["likely"]:g}% / {m["large"]:g}% (small / likely / large)'),
            ("Formula", escape(calc["formula"])),
            *([("Band", escape(calculator.band_note(m["small"], m["large"])))] if m["label"] == calculator.SCENARIO else []),
            ("Result (SCENARIO range)", f'worst {g(res["worst"])}, likely {g(res["likely"])}, best {g(res["best"])}'),
            ("Assumptions", escape("; ".join(f"{k} = {v:g}" for k, v in calc["assumptions"].items())))]


def _calc_html(item):
    rows = calc_rows(item)
    if not rows:
        return ""
    return ('<div class="step"><b class="t">GBP calculation record (Python, core/calculator.py - never the AI)</b><table>'
            + "".join(f"<tr><th style='width:210px'>{k}</th><td>{v}</td></tr>" for k, v in rows) + "</table></div>")


def _decision_html(item, decs):
    d = decs.get(item["delegation"]["work_id"])
    if not d:
        return ""
    outcome = d["outcome"] or {}
    if not d["versions"]:
        return f'<div class="step"><b class="t">Decision</b>{pill(unverified.LABEL, "warn")} {escape(outcome.get("error", "no decision produced"))}</div>'
    first, final = d["versions"][0], d["versions"][-1]
    out = [f'<div class="step"><b class="t">First proposal (Decision Maker)</b>{escape(dec.describe(first["kind"], first["share_pct"]))} - {escape(first["rationale"])}</div>']
    if dec.is_unresolved(d):
        out.append(f'<div class="step"><b class="t">Red Team</b>{pill("challenge unresolved", "warn")} {escape(dec.unresolved_label(d))}</div>')
    elif outcome.get("status") in ("NOT_CHALLENGED", "UNCHALLENGED"):
        out.append(f'<div class="step"><b class="t">Red Team</b>{pill(unverified.LABEL, "warn")} The Red Team did not run; this proposal is unreviewed.</div>')
    objs = sorted(d["objections"], key=lambda o: ({"high": 0, "medium": 1, "low": 2}[o["severity"]], int(o["id"][1:])))
    if objs:
        rows = ""
        for o in objs[:5]:
            resp = o.get("response") or {}
            answer = str(resp.get("action", "")).title() + (": " + resp["reason"] if resp.get("reason") else "")
            rows += (f'<tr><td>{pill(o["severity"].upper(), "bad" if o["severity"] == "high" else "warn")}</td>'
                     f'<td><b>{escape(o["type"].replace("_", " "))}</b> ({escape(", ".join(o["evidence_refs"]))})<br>{escape(o["text"])}</td>'
                     f'<td>{escape(answer)}</td><td>{escape(str(o.get("status") or o.get("handling") or ""))}</td></tr>')
        out.append('<div class="step"><b class="t">Red Team challenge and the written reason</b><table><tr><th>Severity</th><th>Objection</th>'
                   '<th>Decision Maker response</th><th>Status</th></tr>' + rows + "</table></div>")
    elif outcome.get("status") == "RECOMMENDED":
        out.append('<div class="step"><b class="t">Red Team</b>Reviewed; no high or medium objection raised.</div>')
    verdict = {"CONTESTED": pill("Contested - needs CFO judgement", "bad"), "RECOMMENDED": pill("Recommended", "ok")}.get(
        outcome.get("status"), pill("Challenge unresolved" if dec.is_unresolved(d) else outcome.get("status", "?"), "warn"))
    counts = (f"{len(d['objections'])} objection(s) raised, challenge unresolved" if dec.is_unresolved(d)
              else f"{outcome.get('objection_count', 0)} objection(s), {outcome.get('rounds', 0)} round(s)")
    changed = f' (first proposal was {escape(dec.describe(first["kind"], first["share_pct"]))})' if outcome.get("changed") else ""
    out.append(f'<div class="step"><b class="t">Final revision</b>{verdict} <b>{escape(dec.describe(final["kind"], final["share_pct"]))}</b>{changed}'
               f'<br>{escape(final["rationale"])}<div class="sub">{counts}</div></div>')
    for v in (final, first) if first is not final else (final,):
        if v["kind"] in dec.UNQUOTED:
            out.append(f'<div class="step"><b class="t">{escape(dec.describe(v["kind"], v["share_pct"]))}: cost not quantified</b>'
                       + "".join(f"<div>{escape(line)}</div>" for line in dec.lock_lines(item["impact"], v["share_pct"], v["rationale"])) + "</div>")
            break
    return "".join(out)


def _guidance_html(item, world, verified):
    """'What would change this decision' (core/next_steps.py, plain Python, no model)."""
    if verified:
        g = next_steps.for_decision(item, world)
        rows = [("Confidence (" + escape(item["impact"]["confidence"]) + ")", g["confidence_meaning"]), ("Raise confidence", g["raise"]), ("Re-run trigger", g["rerun"])]
    else:
        rows = [("Confidence", next_steps.CONFIDENCE_MEANING), ("Next step", next_steps.UNVERIFIED_NEXT_STEP)]
    return ('<div class="step"><b class="t">What would change this decision</b><table>'
            + "".join(f"<tr><th style='width:150px'>{k}</th><td>{escape(v)}</td></tr>" for k, v in rows) + "</table></div>")


def build_evidence_page(report, world, flags=None, stats=None, evidence_date=None, trace_file=""):
    flags = flags or {}
    stats = stats or {}
    run, decs = report["run"], report.get("decisions") or {}
    items = [i for i in report["items"] if i["delegation"].get("action") == "run"]
    passed = [i for i in items if i["delegation"].get("status") == "PASS" and i["impact"]]
    cnv = unverified.collect(report)
    cnv_ids = {u["work_id"] for u in cnv}
    h = ["<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
         f"<title>Vidhur Evidence Trail</title><style>{CSS}</style></head><body><main>",
         "<h1>Vidhur - evidence trail</h1>",
         f"<div class='sub'>Ganga AI Solutions (synthetic data) - run {escape(str(run.get('run_id', '')))} - {run.get('elapsed_seconds', '?')} s - "
         f"{stats.get('llm_ok', '?')} model calls ok, {stats.get('llm_fail', '?')} failed attempts (retried) - trace <code>{escape(trace_file)}</code></div>"]
    if evidence_date:
        h.append(f"<div class='banner'>Evidence cached on {escape(evidence_date)} (real news saved that day). FX rates are live. Demo mode: three drivers, "
                 "one planted weak claim, one forced-aggressive first decision.</div>")
    elif flags.get("weak") or flags.get("red_team"):
        h.append("<div class='banner'>Demo flag on: " + escape(", ".join(k for k in ("weak", "red_team") if flags.get(k))) + ". Model calls and checks are real.</div>")
    if cnv:
        h.append(f"<div class='banner'>Partial result: {len(cnv)} driver(s) are labelled {escape(unverified.LABEL)} - {escape(unverified.NO_FIGURE)}.</div>")

    h.append("<h2>CFO brief summary</h2><div class='card'><table><tr><th>Driver</th><th>Part</th><th>Status</th><th>Worst</th><th>Likely</th>"
             "<th>Best</th><th>Confidence</th><th>Recommendation</th></tr>")
    for i in items:
        d = i["delegation"]
        c = i["attempts"][-1]["claim"] if i["attempts"] else None
        name = escape((c.driver or c.claim)[:70]) if c else escape(d["analyst"].replace("_", " ") + " analyst")
        part = escape(f"{c.part_name} ({c.supplier_country})") if c else escape(d.get("part_id", ""))
        if d.get("status") == "PASS" and i["impact"]:
            m = i["impact"]
            dv = decs.get(d["work_id"], {})
            fin = dv["versions"][-1] if dv.get("versions") else None
            rec = escape(dec.describe(fin["kind"], fin["share_pct"])) if fin else "-"
            rec += (" " + pill("challenge unresolved", "warn") if dec.is_unresolved(dv) else
                    {"CONTESTED": " " + pill("contested", "bad"), "NOT_CHALLENGED": " " + pill("unreviewed", "warn")}.get((dv.get("outcome") or {}).get("status"), ""))
            h.append(f"<tr><td>{d['work_id']} {name}</td><td>{part}</td><td>{status_pill('PASS')}</td><td class='num'>{g(m['worst_gbp'])}</td>"
                     f"<td class='num'>{g(m['likely_gbp'])}</td><td class='num'>{g(m['best_gbp'])}</td><td>{escape(m['confidence'])}</td><td>{rec}</td></tr>")
        else:
            label = pill(unverified.LABEL, "warn") if d["work_id"] in cnv_ids else "No material signal"
            h.append(f"<tr><td>{d['work_id']} {name}</td><td>{part}</td><td>{status_pill(d.get('status'))}</td>"
                     f"<td colspan='5'>{label} - {escape(unverified.NO_FIGURE)}</td></tr>")
    h.append("</table>")
    if passed:
        tot = [sum(i["impact"][k] for i in passed) for k in ("worst_gbp", "likely_gbp", "best_gbp")]
        extra = f" - excludes {len(cnv)} driver(s) that could not be verified" if cnv else ""
        h.append(f"<p><b>Verified total (change in quarterly cost, scenario range - not a probability):</b> worst {g(tot[0])} / likely {g(tot[1])} / best {g(tot[2])}{extra}.</p>")
    else:
        h.append("<p><b>No driver passed the Evaluator, so no GBP figure is put in front of the CFO.</b></p>")
    h.append(f"<p class='sub'>{escape(NOTE)}</p></div><h2>Evidence trail, driver by driver</h2>")

    for i in items:
        d = i["delegation"]
        h.append(f"<div class='card'><h3>{d['work_id']} - {escape(d['analyst'].replace('_', ' '))} analyst on {escape(d.get('part_id', ''))} {status_pill(d.get('status'))}</h3>"
                 f"<div class='sub'>Plan: {escape(d.get('reason', ''))}</div>")
        why = escape(d.get("note") or d.get("error") or "no claim was produced")
        if not i["attempts"]:
            h.append(f"<div class='step'><b class='t'>{escape(unverified.LABEL)}</b>{why} - {escape(unverified.NO_FIGURE)}.</div>"
                     + (_guidance_html(i, world, False) if d["work_id"] in cnv_ids else "") + "</div>")
            continue
        h.append(_attempts_html(i, world))
        if d.get("status") != "PASS":
            h.append(f"<div class='step'><b class='t'>{escape(unverified.LABEL)}</b>{why} - {escape(unverified.NO_FIGURE)}.</div>")
        h.append(_calc_html(i))
        h.append(_decision_html(i, decs))
        if d.get("status") == "PASS" and i["impact"]:
            h.append(_guidance_html(i, world, True))
        elif d["work_id"] in cnv_ids:
            h.append(_guidance_html(i, world, False))
        h.append("</div>")
    h.append("</main></body></html>")
    return "".join(h)
