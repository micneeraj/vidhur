"""Small helpers shared by the two evaluation scripts: read what the model runtime did out of a trace file."""

import json
from pathlib import Path


def read_events(trace_path):
    path = Path(trace_path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def llm_facts_for(trace_path, case_id):
    """Model that answered, routed model / status / receipt id of failed attempts, read from the trace for one case."""
    out = {"answered_model": None, "routed_model": None, "receipt_id": None, "llm_failed_attempts": 0, "status_codes": []}
    for e in read_events(trace_path):
        if e.get("target") != case_id or not e["step"].startswith("llm:"):
            continue
        if e["step"].endswith(":error"):
            out["llm_failed_attempts"] += 1
            out["routed_model"] = e.get("routed_model") or out["routed_model"]
            out["receipt_id"] = e.get("receipt_id") or out["receipt_id"]
            out["status_codes"].append(e.get("status_code"))
        else:
            out["answered_model"] = e.get("answered_model")
            out["receipt_id"] = e.get("receipt_id") or out["receipt_id"]
    return out


def llm_summary(trace_path):
    """Whole-trace summary: successful calls, failed attempts, HTTP statuses, routed models, models that answered, receipt ids."""
    s = {"ok": 0, "failed_attempts": 0, "status_codes": {}, "routed_models": set(), "answered_models": set(), "receipt_ids": []}
    for e in read_events(trace_path):
        if not e["step"].startswith("llm:"):
            continue
        if e["step"].endswith(":error"):
            s["failed_attempts"] += 1
            code = str(e.get("status_code"))
            s["status_codes"][code] = s["status_codes"].get(code, 0) + 1
            if e.get("routed_model"):
                s["routed_models"].add(e["routed_model"])
        else:
            s["ok"] += 1
            if e.get("answered_model"):
                s["answered_models"].add(e["answered_model"])
        if e.get("receipt_id"):
            s["receipt_ids"].append(e["receipt_id"])
    s["routed_models"], s["answered_models"] = sorted(s["routed_models"]), sorted(s["answered_models"])
    return s
