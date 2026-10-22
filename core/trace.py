"""Append-only JSONL trace of every step Vidhur takes.

One file per run: logs/run_<timestamp>.jsonl. Each line is one event.
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.config import ROOT


class Trace:
    def __init__(self, log_dir=None, quiet=False):
        self.run_id = uuid.uuid4().hex[:8]
        self.quiet = quiet
        log_dir = Path(log_dir) if log_dir else ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = log_dir / f"run_{stamp}.jsonl"

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
        }
        record.update(extra)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if not self.quiet:
            self._print(record)
        return record

    @staticmethod
    def _print(record):
        stamp = record["time"][11:19]
        parts = [f"[{stamp}] {record['agent']:<12} {record['step']}"]
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
