"""The CFO dashboard: ONE self-contained HTML file (inline CSS + small vanilla JS, no CDN, works offline) built ONLY from saved run files.

Per run it reads the saved world state (the same report the evidence trail page uses), the JSONL trace (for the model name) and the
folder's evidence_trail.html link. Every number on the page is read from those files or summed from them in Python; an element whose
value is not in the files is left out. Nothing here calls a model or a service.
"""

import json
import re
from datetime import datetime
from html import escape
from pathlib import Path

from core import decisions as dec
from core import next_steps, unverified
from core.config import ROOT
from core.evidence_page import _checks_html, _sources_html, calc_rows, g
from core.shared_state import RunWorld
from core.source_trust import trust_of

OUT_FILE = ROOT / "output_examples" / "dashboard.html"
RUNS = [  # (button label, folder relative to output_examples)
    ("v3 run", "demo_run_20261024_v3"),
    ("Live run - could not verify", "live_run_20261024_could_not_verify"),
]
QUESTION = "Why will our quarterly cost change, by how much in GBP, and what should we do?"
CONFIDENCE_LINE = {"Low": "Low = investigate before committing money.", "Medium": "Medium = act with care.",
                   "High": "High = fine for routine action or monitoring."}

CSS = """
:root{--bg:#0f1a2b;--panel:#16243a;--panel2:#1c2d47;--line:#2a3d5c;--ink:#f1f5f9;--mute:#9fb0c7;--teal:#2dd4bf;--ok:#34d399;--okbg:#0f3a2e;
--warn:#fbbf24;--warnbg:#3d3010;--bad:#f87171;--badbg:#4a1d1d;--blue:#7dd3fc}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.55 system-ui,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1360px;margin:0 auto;padding:28px 32px 70px}
header{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:16px}
.brand{font-size:30px;font-weight:800;letter-spacing:.02em;color:var(--teal)}
.sub{color:var(--mute);font-size:15px}
.switch{display:flex;flex-wrap:wrap;gap:8px}
.switch button{background:var(--panel);color:var(--mute);border:1px solid var(--line);border-radius:99px;padding:8px 18px;font:600 15px system-ui;cursor:pointer}
.switch button.on{background:var(--teal);color:#06231f;border-color:var(--teal)}
.question{font-size:clamp(26px,3.2vw,42px);line-height:1.2;font-weight:800;margin:34px 0 14px;max-width:1100px}
.answer{background:var(--panel);border:1px solid var(--line);border-left:6px solid var(--teal);border-radius:16px;padding:22px 26px;margin:0 0 20px}
.answer .big{font-size:clamp(24px,2.6vw,34px);font-weight:800}
.answer .small{color:var(--mute);font-size:16px;margin-top:2px}
.answer .acts{margin-top:10px;font-size:19px}
.chips{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 26px}
.chip{background:var(--panel2);border:1px solid var(--line);border-radius:99px;padding:5px 14px;font-size:14px;color:var(--ink)}
.chip.t{border-color:var(--teal);color:var(--teal)}.chip.w{border-color:var(--warn);color:var(--warn)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr));gap:20px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:22px 24px;cursor:pointer;display:flex;flex-direction:column;gap:12px}
.card:hover,.card.open{border-color:var(--teal)}
.card h3{margin:0;font-size:21px;line-height:1.3}.card .part{color:var(--mute);font-size:15px}
.badge{display:inline-block;border-radius:99px;padding:3px 13px;font-size:13px;font-weight:800;letter-spacing:.04em}
.b-ok{background:var(--okbg);color:var(--ok)}.b-warn{background:var(--warnbg);color:var(--warn)}.b-bad{background:var(--badbg);color:var(--bad)}
.b-info{background:#12304a;color:var(--blue)}
.likely{font-size:40px;font-weight:800;line-height:1.1;font-variant-numeric:tabular-nums}
.range{color:var(--mute);font-size:15px;font-variant-numeric:tabular-nums}
.nogbp{color:var(--warn);font-weight:700;font-size:18px}
.rec{font-size:19px;font-weight:700}
.hint{color:var(--teal);font-size:13px;margin-top:auto}
.chain{display:none;background:var(--panel);border:1px solid var(--teal);border-radius:18px;padding:26px 30px;margin:20px 0}
.chain.open{display:block}.nojs .chain{display:block}
.chain h2{margin:0 0 4px;font-size:22px}
.tl{position:relative;margin:18px 0 0;padding-left:38px}
.tl:before{content:"";position:absolute;left:11px;top:6px;bottom:6px;width:2px;background:var(--line)}
.st{position:relative;padding:0 0 22px}
.st:before{content:"";position:absolute;left:-33px;top:5px;width:14px;height:14px;border-radius:50%;background:var(--teal);border:3px solid var(--panel)}
.st.stop:before{background:var(--warn)}.st.fail:before{background:var(--bad)}
.st .h{font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--teal);font-weight:800;margin-bottom:4px}
.st.stop .h{color:var(--warn)}.st.fail .h{color:var(--bad)}
.st p{margin:4px 0}.sm{color:var(--mute);font-size:14px}
table{border-collapse:collapse;width:100%;font-size:15px;margin:6px 0}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--mute);font-size:12px;text-transform:uppercase;letter-spacing:.04em;font-weight:700}
.pill{display:inline-block;padding:1px 10px;border-radius:99px;font-size:12px;font-weight:800}
.pill.ok{background:var(--okbg);color:var(--ok)}.pill.bad{background:var(--badbg);color:var(--bad)}.pill.warn{background:var(--warnbg);color:var(--warn)}
.pill.info{background:#12304a;color:var(--blue)}
a{color:var(--blue);word-break:break-all}.num{font-variant-numeric:tabular-nums}
.tblwrap{overflow-x:auto}
footer{margin-top:40px;border-top:1px solid var(--line);padding-top:22px;color:var(--mute);font-size:15px}
footer .legend{display:flex;flex-wrap:wrap;gap:18px;margin:10px 0}footer b{color:var(--ink)}
.run{display:none}.run.on{display:block}.nojs .run{display:block;margin-bottom:60px}
@media (max-width:700px){.wrap{padding:18px 14px 50px}body{font-size:16px}.likely{font-size:32px}.chain{padding:18px 16px}}
"""

JS = """
(function(){
  var d=document;d.body.classList.remove('nojs');
  var runs=d.querySelectorAll('.run'),btns=d.querySelectorAll('.switch button');
  function showRun(id){runs.forEach(function(r){r.classList.toggle('on',r.id===id)});btns.forEach(function(b){b.classList.toggle('on',b.dataset.run===id)});
    try{history.replaceState(null,'','#'+id)}catch(e){}}
  btns.forEach(function(b){b.addEventListener('click',function(){showRun(b.dataset.run)})});
  function openChain(card){var run=card.closest('.run');
    run.querySelectorAll('.card').forEach(function(c){c.classList.toggle('open',c===card)});
    run.querySelectorAll('.chain').forEach(function(c){c.classList.toggle('open',c.id===card.dataset.chain)});}
  d.querySelectorAll('.card').forEach(function(c){
    c.addEventListener('click',function(e){if(e.target.closest('a'))return;openChain(c);var t=d.getElementById(c.dataset.chain);if(t&&t.scrollIntoView)t.scrollIntoView({block:'nearest'})});
    c.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();c.click()}});});
  var first=location.hash.slice(1),ids=[].map.call(runs,function(r){return r.id});
  var def=d.querySelector('.switch button[data-default]');showRun(ids.indexOf(first)>=0?first:(def?def.dataset.run:ids[0]));
  runs.forEach(function(r){var c=r.querySelector('.card');if(c)openChain(c)});
})();
"""


def _pill(text, kind):
    return f'<span class="pill {kind}">{escape(str(text))}</span>'


def _short(s, n=100):
    """Cut at a word boundary with an ellipsis (never mid-word)."""
    s = str(s)
    return s if len(s) <= n else s[:n].rsplit(" ", 1)[0].rstrip(" ,;:(") + "..."


def _word(s):
    return escape(str(s).replace("_", " "))


# ------------------------------------------------------------------ loading
def load_run(folder, label=None, run_id=None, world_path=None, trace_path=None, trail_href=None):
    """Read one run's saved files from `folder` (world_state_<id>.json, one *.jsonl trace, evidence_trail.html). Returns a dict or None."""
    folder = Path(folder)
    worlds = [Path(world_path)] if world_path else sorted(folder.glob("world_state_*.json"))
    if not worlds or not worlds[0].exists():
        return None
    wp = worlds[0]
    rid = run_id or wp.stem.replace("world_state_", "")
    world = RunWorld(rid, wp)
    traces = [Path(trace_path)] if trace_path else sorted(folder.glob("run_*.jsonl"))
    models = []
    for tp in traces:
        if tp.exists():
            for line in tp.read_text(encoding="utf-8").splitlines():
                try:
                    m = json.loads(line).get("answered_model")
                except ValueError:
                    continue
                if m and m not in models:
                    models.append(m)
    if trail_href is None:
        trail_href = f"{folder.name}/evidence_trail.html" if (folder / "evidence_trail.html").exists() else None
    return {"id": f"run-{rid}", "label": label or rid, "run_id": rid, "world": world, "report": world.report(), "models": models,
            "trail": trail_href}


def is_real(run):
    """A run is shown only if it has at least one driver and at least one model call recorded in its own files (never an empty or fake run)."""
    r = run["report"]
    return bool(_drivers(r)) and (r["run"].get("llm_ok") or 0) > 0


def default_runs(base=None):
    base = Path(base) if base else ROOT / "output_examples"
    return [r for r in (load_run(base / folder, label) for label, folder in RUNS) if r]


# ------------------------------------------------------------------ derived facts (all from the report)
def _sent_back(item):
    return sum(1 for a in item["attempts"] for v in a["verdicts"] if v.get("verdict") == "SEND_BACK")


def _final(report, item):
    d = (report.get("decisions") or {}).get(item["delegation"]["work_id"]) or {}
    return d, (d["versions"][-1] if d.get("versions") else None)


def _rt_summary(report, item):
    """("changed" | "defended" | None, n_objections) for one driver."""
    d, _ = _final(report, item)
    out = d.get("outcome") or {}
    objs = d.get("objections") or []
    if out.get("changed"):
        return "changed", len(objs)
    if objs and all((o.get("response") or {}).get("action", "").upper() == "DEFEND" for o in objs):
        return "defended", len(objs)
    return None, len(objs)


def _label(item):
    c = item["attempts"][-1]["claim"] if item["attempts"] else None
    return c.supplier_country if c else item["delegation"].get("part_id", "")


def _best_tier(world, claim):
    tiers = []
    for url in dict.fromkeys(claim.source_urls):
        art = world.article(url)
        if art:
            tiers.append(trust_of(url, art["publisher"])[1])
    return len(tiers), (min(tiers) if tiers else None)


def _drivers(report):
    """[(item, verified?)] for every driver shown as a card."""
    cnv = {u["work_id"] for u in unverified.collect(report)}
    out = []
    for item in report["items"]:
        d = item["delegation"]
        if d.get("action") != "run":
            continue
        if d.get("status") == "PASS" and item["impact"]:
            out.append((item, True))
        elif d["work_id"] in cnv:
            out.append((item, False))
    return out


# ------------------------------------------------------------------ page parts
def _answer(report, drivers):
    ver = [i for i, ok in drivers if ok]
    acts = []
    for item, ok in drivers:
        if ok:
            _, fin = _final(report, item)
            acts.append(f"<b>{escape(_label(item))}:</b> {escape(dec.describe(fin['kind'], fin['share_pct']).lower() if fin else 'no decision produced')}")
        else:
            acts.append(f"<b>{escape(_label(item))}:</b> could not verify, no GBP figure (not zero impact)")
    if ver:
        t = [sum(i["impact"][k] for i in ver) for k in ("likely_gbp", "worst_gbp", "best_gbp")]
        note = f" - excludes {len(drivers) - len(ver)} driver(s) that could not be verified" if len(ver) < len(drivers) else ""
        head = (f'<div class="big">Verified quarterly cost change, likely {g(t[0])}</div>'
                f'<div class="small">worst {g(t[1])} / best {g(t[2])} - scenario range, not a probability{escape(note)}</div>')
    else:
        head = '<div class="big">No driver is verified, so no GBP figure is shown</div>'
    return f'<div class="answer">{head}<div class="acts">{" &nbsp;|&nbsp; ".join(acts)}</div></div>'


def _proof_chips(run, report, drivers):
    chips = []
    sent = sum(_sent_back(i) for i, _ in drivers)
    chips.append(f'<span class="chip t">{sent} claim{"s" if sent != 1 else ""} sent back by the Evaluator</span>')
    changed = sum(1 for i, ok in drivers if ok and _rt_summary(report, i)[0] == "changed")
    objs = sum(_rt_summary(report, i)[1] for i, ok in drivers if ok)
    if changed:
        chips.append(f'<span class="chip t">{changed} decision{"s" if changed != 1 else ""} changed by the Red Team</span>')
    elif objs and all(_rt_summary(report, i)[0] == "defended" for i, ok in drivers if ok and _rt_summary(report, i)[1]):
        chips.append(f'<span class="chip t">Red Team objections defended ({objs})</span>')
    chips.append('<span class="chip t">0 GBP numbers written by AI (all GBP calculated in Python, core/calculator.py)</span>')
    r = report["run"]
    if "llm_ok" in r:
        model = f" - {escape(', '.join(run['models']))}" if run["models"] else ""
        fail = r.get("llm_fail")
        extra = f", {fail} failed attempt{'s' if fail != 1 else ''}" + (" (retried)" if fail else "") if fail is not None else ""
        chips.append(f'<span class="chip">{r["llm_ok"]} model calls ok{extra}{model}</span>')
    if r.get("elapsed_seconds") is not None:
        chips.append(f'<span class="chip">run time {r["elapsed_seconds"]} s</span>')
    return '<div class="chips">' + "".join(chips) + "</div>"


def _card(run, report, item, ok, chain_id):
    d = item["delegation"]
    c = item["attempts"][-1]["claim"] if item["attempts"] else None
    name = _short(c.driver or c.claim) if c else d.get("analyst", "") + " analyst"
    part = f"{c.part_name} ({c.supplier_country})" if c else d.get("part_id", "")
    h = [f'<div class="card" tabindex="0" role="button" aria-controls="{chain_id}" data-chain="{chain_id}">',
         f'<div><span class="badge {"b-ok" if ok else "b-warn"}">{"VERIFIED" if ok else "COULD NOT VERIFY"}</span></div>',
         f'<div><h3>{escape(d["work_id"])} - {escape(name)}</h3><div class="part">{escape(part)}</div></div>']
    chips = []
    sb = _sent_back(item)
    if sb:
        chips.append(f'<span class="chip w">Sent back {sb}x</span>')
    if ok:
        m = item["impact"]
        h.append(f'<div><div class="likely">{g(m["likely_gbp"])}</div><div class="range">likely - worst {g(m["worst_gbp"])} / best {g(m["best_gbp"])} - '
                 'scenario range, not a probability</div></div>')
        conf = m["confidence"]
        h.append(f'<div><span class="badge b-info">{escape(conf)} confidence</span> <span class="sm">{escape(CONFIDENCE_LINE.get(conf, ""))}</span></div>')
        dv, fin = _final(report, item)
        if fin:
            st = (dv.get("outcome") or {}).get("status")
            extra = ' <span class="pill bad">contested</span>' if st == "CONTESTED" else ' <span class="pill warn">unreviewed</span>' if st == "NOT_CHALLENGED" else ""
            h.append(f'<div class="rec">{escape(dec.describe(fin["kind"], fin["share_pct"]))}{extra}</div>')
        how, n = _rt_summary(report, item)
        if how == "changed":
            chips.append('<span class="chip t">Red Team changed it</span>')
        elif how == "defended":
            chips.append(f'<span class="chip t">Red Team objections defended ({n})</span>')
        n_src, tier = _best_tier(run["world"], c)
        if n_src:
            chips.append(f'<span class="chip">{n_src} sources, best Tier {tier}</span>')
    else:
        h.append('<div class="nogbp">No GBP figure until verified - this is NOT zero impact</div>')
        h.append('<div class="sm">Where it stopped: ' + escape(unverified.collect(report) and next(u["reason"] for u in unverified.collect(report)
                                                                                            if u["work_id"] == d["work_id"])) + "</div>")
    h.append('<div class="chips" style="margin:0">' + "".join(chips) + "</div>" if chips else "")
    h.append('<div class="hint">Click to see the why chain below</div></div>')
    return "".join(h)


def _step(n, title, body, kind=""):
    return f'<div class="st {kind}"><div class="h">{n}. {escape(title)}</div>{body}</div>'


def _claim_body(c):
    rng = f"{c.pct_low:g}-{c.pct_high:g}%"
    return (f"<p>{escape(c.claim)}</p><p class='sm'>{_word(c.direction)} {escape(rng)} - label: <b>{escape(c.pct_label or 'unlabelled')}</b> - "
            f"attempt {c.attempt}</p>")


def _verdict_body(v):
    ok = v["verdict"] == "PASS"
    how = f", model check: {v['strength']}" if v.get("llm_checked") else ", model check skipped because a deterministic check failed"
    return (f'<div class="tblwrap">{_checks_html(v)}</div><p>{_pill("PASS" if ok else "SENT BACK", "ok" if ok else "bad")} '
            f'<span class="sm">{v["independent_sources"]} independent source(s){escape(how)}</span></p>')


def _chain(run, report, item, ok, chain_id, is_open=False):
    d, world = item["delegation"], run["world"]
    steps, n = [], 0

    def add(title, body, kind=""):
        nonlocal n
        n += 1
        steps.append(_step(n, title, body, kind))

    for a in item["attempts"]:
        c = a["claim"]
        first = c.attempt == 1
        add("Claim" if first else f"Revised claim (attempt {c.attempt})", _claim_body(c))
        add("Sources", f'<div class="tblwrap">{_sources_html(world, c)}</div>')
        for v in a["verdicts"]:
            add("Evaluator checks", _verdict_body(v), "" if v["verdict"] == "PASS" else "fail")
            if v["verdict"] == "SEND_BACK":
                add("Sent back", "<p>" + "<br>".join(f"<b>{escape(r.split(':', 1)[0])}</b>{escape(': ' + r.split(':', 1)[1]) if ':' in r else ''}" for r in v["reasons"])
                    + "</p><p class='sm'>The Evaluator returned the claim to the analyst with this reason.</p>", "fail")
    calc = calc_rows(item) if ok else []
    if calc:
        add("GBP calculation - calculated in Python, never by the AI",
            '<div class="tblwrap"><table>' + "".join(f"<tr><th style='width:210px'>{k}</th><td>{v}</td></tr>" for k, v in calc) + "</table></div>")
    dv, fin = _final(report, item)
    if ok and dv.get("versions"):
        first = dv["versions"][0]
        add("First proposal (Decision Maker)", f"<p><b>{escape(dec.describe(first['kind'], first['share_pct']))}</b></p><p>{escape(first['rationale'])}</p>")
        objs = sorted(dv.get("objections") or [], key=lambda o: ({"high": 0, "medium": 1, "low": 2}[o["severity"]], int(o["id"][1:])))
        if objs:
            rows = ""
            for o in objs:
                resp = o.get("response") or {}
                ans = (str(resp.get("action", "")).upper() + (": " + resp["reason"] if resp.get("reason") else "")) if resp else ""
                rows += (f'<tr><td>{_pill(o["severity"].upper(), "bad" if o["severity"] == "high" else "warn")}</td>'
                         f'<td><b>{_word(o["type"])}</b> ({escape(", ".join(o["evidence_refs"]))})<br>{escape(o["text"])}</td>'
                         f'<td>{escape(ans)}</td><td>{escape(str(o.get("status") or o.get("handling") or ""))}</td></tr>')
            add("Red Team objections and the Decision Maker's written reasons",
                '<div class="tblwrap"><table><tr><th>Severity</th><th>Objection</th><th>Response</th><th>Status</th></tr>' + rows + "</table></div>")
        elif (dv.get("outcome") or {}).get("status") == "NOT_CHALLENGED":
            add("Red Team", "<p>The Red Team did not run; this proposal is unreviewed.</p>", "stop")
        else:
            add("Red Team", "<p>Reviewed; no high or medium objection raised.</p>")
        out = dv.get("outcome") or {}
        status = {"CONTESTED": _pill("Contested - needs CFO judgement", "bad"), "RECOMMENDED": _pill("Recommended", "ok")}.get(
            out.get("status"), _pill(out.get("status", "?"), "warn"))
        chg = f" (first proposal was {escape(dec.describe(first['kind'], first['share_pct']))})" if out.get("changed") else " (unchanged by the challenge)"
        add("Final decision", f"<p>{status} <b>{escape(dec.describe(fin['kind'], fin['share_pct']))}</b>{chg}</p><p>{escape(fin['rationale'])}</p>"
            f"<p class='sm'>{out.get('objection_count', 0)} objection(s), {out.get('rounds', 0)} round(s)</p>")
        gd = next_steps.for_decision(item, world)
        rows = [("Confidence (" + escape(item["impact"]["confidence"]) + ")", gd["confidence_meaning"]), ("Raise confidence", gd["raise"]), ("Re-run trigger", gd["rerun"])]
        add("What would change this decision", "<table>" + "".join(f"<tr><th style='width:160px'>{k}</th><td>{escape(v)}</td></tr>" for k, v in rows) + "</table>")
    elif not ok:
        why = escape(d.get("note") or d.get("error") or "no supported claim was produced")
        add("Chain stopped here - " + unverified.LABEL, f"<p>{why}</p><p><b>{escape(unverified.NO_FIGURE)}.</b> No calculation, decision or Red Team step "
            "was run for this driver, because nothing was verified.</p>", "stop")
        gd = next_steps.for_unverified()
        add("What would change this decision", "<table>" + f"<tr><th style='width:160px'>Confidence</th><td>{escape(gd['confidence_meaning'])}</td></tr>"
            f"<tr><th>Next step</th><td>{escape(gd['next_step'])}</td></tr></table>")
    c = item["attempts"][-1]["claim"] if item["attempts"] else None
    title = f"Why chain - {d['work_id']}: {_short(c.driver or c.claim) if c else ''}"
    return (f'<section class="chain{" open" if is_open else ""}" id="{chain_id}"><h2>{escape(title)}</h2>'
            f'<div class="sm">Every step is read from the saved run files.</div><div class="tl">{"".join(steps)}</div></section>')


def _run_section(run):
    report = run["report"]
    drivers = _drivers(report)
    rid = run["id"]
    parts = [f'<div class="run" id="{rid}">', _answer(report, drivers), _proof_chips(run, report, drivers)]
    if drivers:
        parts.append('<div class="cards">' + "".join(_card(run, report, i, ok, f"{rid}-chain-{i['delegation']['work_id']}") for i, ok in drivers) + "</div>")
        parts.extend(_chain(run, report, i, ok, f"{rid}-chain-{i['delegation']['work_id']}") for i, ok in drivers)
    else:
        parts.append('<p class="sm">This run has no driver to show.</p>')
    link = f'<a href="{escape(run["trail"])}">evidence_trail.html for this run</a>' if run["trail"] else ""
    parts.append(f'<div class="sm" style="margin-top:18px">Full detail: {link}</div></div>' if link else "</div>")
    return "".join(parts)


def build_dashboard_html(runs):
    runs = [r for r in runs if is_real(r)]
    subtitle = "Ganga AI Solutions (synthetic company data; news and FX are live)"
    buttons = "".join(f'<button type="button" data-run="{r["id"]}"{' data-default="1"' if r is runs[0] else ""}>{escape(r["label"])}</button>' for r in runs)
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Vidhur CFO Dashboard</title><style>{CSS}</style></head><body class='nojs'><div class='wrap'>"
            f"<header><div><div class='brand'>Vidhur</div><div class='sub'>{escape(subtitle)}</div></div><div class='switch'>{buttons}</div></header>"
            f"<h1 class='question'>{escape(QUESTION)}</h1>" + "".join(_run_section(r) for r in runs) +
            "<footer><div class='legend'><span><b>OBSERVED</b> = read from data (Zetaris rows, ECB rates)</span>"
            "<span><b>FORECAST</b> = stated by a cited source</span><span><b>SCENARIO</b> = our assumption (data/decision_assumptions.csv)</span></div>"
            "<div><b>Built by a commercial auditor - no number without evidence, no action without challenge.</b></div></footer>"
            f"</div><script>{JS}</script></body></html>")


def write_dashboard(runs=None, out=None):
    runs = [r for r in (runs if runs is not None else default_runs()) if is_real(r)]
    out = Path(out) if out else OUT_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_dashboard_html(runs), encoding="utf-8")
    return out


def latest_run(world, trace_path, report=None):
    """The run that has just finished (main.py): reads the same files from logs/, links output/evidence_trail.html."""
    if not Path(str(world.path)).exists() or (report or {}).get("run", {}).get("status") != "finished":
        return None  # only a real, completed pipeline run is added (partial or fake runs are not)
    run = load_run(Path(world.path).parent, "Latest run (" + datetime.now().strftime("%d %b %H:%M") + ")", run_id=world.run_id,
                   world_path=world.path, trace_path=trace_path, trail_href="../output/evidence_trail.html")
    return run if run and is_real(run) else None
