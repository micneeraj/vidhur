"""Lane pin check: call each Nightshift lane 3 times with a tiny prompt and print which model actually answered.

Usage: python scripts/check_lanes.py
Uses the same client and env vars as the product (METERLESS_BASE_URL, METERLESS_RUNTIME_TOKEN, MODEL_*). Never prints the token.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from core import config, llm  # noqa: E402

CALLS_PER_LANE = 3
LIGHTNING = "nemotron-3.5-lightning"
LANES = {
    "vidhur-analyst": ("analyst", config.analyst_expected),
    "vidhur-evaluator": ("evaluator", config.evaluator_expected),
}
PROMPT = "Reply with the single word: ok"


def main():
    summary = {}
    for lane, (_, expected_fn) in LANES.items():
        answered = []
        for i in range(1, CALLS_PER_LANE + 1):
            try:
                result = llm.chat([{"role": "user", "content": PROMPT}], model=lane, agent="check_lanes", step="ping",
                                  max_tokens=300, expected_model=expected_fn())
                answered.append(result.model)
                print(f"{lane} call {i}: requested={lane} answered={result.model} ({result.latency_ms} ms, {result.completion_tokens} tokens)")
            except Exception as exc:  # report, never hide
                answered.append(None)
                print(f"{lane} call {i}: FAILED {type(exc).__name__}: {str(exc)[:160]}")
        summary[lane] = answered
    print()
    for lane, answered in summary.items():
        non_lightning = sum(1 for m in answered if m and LIGHTNING not in m)
        print(f"{lane}: answered by {sorted({m for m in answered if m})}; non-Lightning {non_lightning}/{CALLS_PER_LANE}")
    ev = summary["vidhur-evaluator"]
    pinned = sum(1 for m in ev if m and LIGHTNING not in m) >= 2
    print("EVALUATOR_LANE_PINNED=" + ("yes" if pinned else "no"))


if __name__ == "__main__":
    main()
