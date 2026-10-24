"""Which agents could not run, and why - read back from the world-state report so the brief and the exit code agree."""

from core import decisions

RUNTIME_ERRORS_TEXT = "AI runtime busy or offline"


def collect(report):
    """List of {"agent", "where", "reason"} for every part of the run that failed because a service was unavailable."""
    found = []
    for s in (report.get("run") or {}).get("stopped") or []:
        found.append({"agent": s["agent"], "where": s["stage"], "reason": s["error"]})
    for item in report.get("items", []):
        d = item["delegation"]
        if d.get("status") == "NOT_ASSESSED":
            found.append({"agent": f"{d.get('analyst', 'analyst')} analyst / Evaluator", "where": f"{d['work_id']} on {d.get('part_id', '?')}",
                          "reason": d.get("error") or "model call failed"})
    for work_id, dec in sorted((report.get("decisions") or {}).items()):
        outcome = dec.get("outcome") or {}
        if decisions.is_unresolved(dec):
            found.append({"agent": "Decision Maker", "where": work_id,
                          "reason": f"response to {len(dec['objections'])} Red Team objection(s) did not finish: {outcome.get('error') or 'model call failed'}"})
        elif outcome.get("status") == "NOT_CHALLENGED":
            found.append({"agent": "Red Team", "where": work_id, "reason": outcome.get("error") or "model call failed"})
        elif outcome.get("status") == "NOT_DECIDED":
            found.append({"agent": "Decision Maker", "where": work_id, "reason": outcome.get("error") or "model call failed"})
    return found


def brief_section(report):
    found = collect(report)
    if not found:
        return []
    out = ["", "## Agents that could not run", "",
           "**This brief is partial: AI runtime busy or offline.** Everything that completed is in the sections above and in the world state; "
           "the agents below could not finish, so what they would have contributed is missing. Run again later.", "",
           "| Agent | Where | Why |", "|---|---|---|"]
    out += [f"| {f['agent']} | {f['where']} | {f['reason'].replace('|', '/')} |" for f in found]
    return out + [""]
