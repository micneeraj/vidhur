"""Append-only JSONL trace of every step Vidhur takes.

One file per run: logs/run_<timestamp>.jsonl. Each line is one event. Analysts run in a thread pool,
so writes are serialized and every event is tagged with the work item (target) that produced it.
"""

import contextvars
import json
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from core.config import ROOT

_current_target = contextvars.ContextVar("vidhur_target", default=None)


class Trace:
    def __init__(self, log_dir=None, quiet=False):
        self.run_id = uuid.uuid4().hex[:8]
        self.quiet = quiet
        self._lock = threading.Lock()
        self.stats = {"llm_ok": 0, "llm_fail": 0}
        log_dir = Path(log_dir) if log_dir else ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = log_dir / f"run_{stamp}.jsonl"

    @contextmanager
    def target(self, label):
        """Tag every event written inside this block (in this thread) with a work-item label."""
        token = _current_target.set(label)
        try:
            yield
        finally:
            _current_target.reset(token)

    def event(
        self,
        agent,
        step,
        input_summary="",
        output_summary="",
        model=None,
        sources=None,
        decision=None,
        retry_count=0,
        **extra,
    ):
        record = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run_id": self.run_id,
            "agent": agent,
            "step": step,
            "input_summary": _short(input_summary),
            "output_summary": _short(output_summary),
            "model": model,
            "sources": sources or [],
            "decision": decision,
            "retry_count": retry_count,
            "target": _current_target.get(),
        }
        record.update(extra)
        with self._lock:
            if step.startswith("llm:"):
                self.stats["llm_fail" if step.endswith(":error") else "llm_ok"] += 1
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            if not self.quiet:
                self._print(record)
        return record

    @staticmethod
    def _print(record):
        stamp = record["time"][11:19]
        tag = f"[{record['target']}] " if record.get("target") else ""
        parts = [f"[{stamp}] {tag}{record['agent']:<12} {record['step']}"]
        if record["decision"]:
            parts.append(f"-> {record['decision']}")
        if record["retry_count"]:
            parts.append(f"(retry {record['retry_count']})")
        if record["output_summary"]:
            parts.append(f"| {_short(record['output_summary'], 110)}")
        print(" ".join(parts))


def _short(value, limit=400):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."
