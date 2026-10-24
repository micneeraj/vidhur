"""The "Could not verify" label: a driver Vidhur looked at but could not confirm is listed as such, never dropped and never shown as zero impact."""

LABEL = "Could not verify"
NO_FIGURE = "no GBP figure - this is NOT zero impact"
# A NO_CLAIM with one of these notes is a real finding (the data was read and nothing material moved), not a failure to verify.
NO_SIGNAL_MARKERS = ("materiality threshold", "mixed direction")


NOT_RUN = "not run in this run (--drivers)"  # a driver left out on purpose: neither zero impact nor "Could not verify"


def not_run(report):
    """Delegations that --drivers left out of this run."""
    return [item["delegation"] for item in report["items"] if item["delegation"].get("action") == "not_run"]


def _reason(d):
    status = d.get("status")
    if status == "NOT_ASSESSED":
        return f"an agent failed after retries: {d.get('error') or 'model call failed'}"
    if status == "UNVERIFIED":
        return d.get("note") or "the claim was still failing the Evaluator's checks after the allowed retries"
    if status == "NO_CLAIM":
        return f"missing evidence: {d.get('note') or 'the analyst found no supported development'}"
    return f"the work item never finished (status {status})"


def collect(report):
    """[{work_id, analyst, part_id, status, reason}] for every delegated driver that did not end PASS (except a real 'no material signal')."""
    out = []
    for item in report["items"]:
        d = item["delegation"]
        if d.get("action") != "run" or d.get("status") == "PASS":
            continue
        if d.get("status") == "NO_CLAIM" and any(m in (d.get("note") or "") for m in NO_SIGNAL_MARKERS):
            continue
        claim = item["attempts"][-1]["claim"] if item["attempts"] else None
        out.append({"work_id": d["work_id"], "analyst": d.get("analyst", "?"), "part_id": d.get("part_id", "?"),
                    "part_name": claim.part_name if claim else "", "country": claim.supplier_country if claim else "",
                    "driver": (claim.driver or claim.claim) if claim else "", "status": d.get("status", "?"), "reason": _reason(d)})
    return out


def no_signal(report):
    """Drivers that were checked and showed no material move (a finding, listed so they are not mistaken for dropped)."""
    return [item["delegation"] for item in report["items"] if item["delegation"].get("action") == "run"
            and item["delegation"].get("status") == "NO_CLAIM"
            and any(m in (item["delegation"].get("note") or "") for m in NO_SIGNAL_MARKERS)]
