"""Shared plumbing for analysts: model call + JSON parsing, the shared article pool, world-state I/O."""

import threading

from core import config, llm

MAX_TOKENS = 4000  # answers are short JSON but the model reasons first (~3k tokens); 4000 still fits the runtime deadline
BRIEF_REASONING = "Keep your private reasoning brief: decide quickly, then answer with the JSON only."


class AnalystBase:
    name = "analyst"

    def __init__(self, trace, world, pool, model=None, demo_weak_claim=False, refresh_news=False, demo_untrusted_source=False):
        self.trace = trace
        self.world = world  # core.shared_state.RunWorld
        self.pool = pool  # url -> Article, shared by all analysts and the Evaluator
        self._pool_lock = threading.Lock()
        self.model = model or config.analyst_model()
        self.expected_model = config.analyst_expected() if model is None else model
        self.demo_weak_claim = demo_weak_claim
        self.demo_untrusted_source = demo_untrusted_source
        self.refresh_news = refresh_news

    def _add_to_pool(self, articles):
        with self._pool_lock:
            for a in articles:
                self.pool[a.url] = a

    def _ask_json(self, system, user, step, retry_count):
        """One model call. Returns (parsed dict or None, LLMResult)."""
        result = llm.chat(
            [{"role": "system", "content": system + " " + BRIEF_REASONING}, {"role": "user", "content": user}],
            model=self.model, expected_model=self.expected_model, agent=self.name, step=step,
            trace=self.trace, retry_count=retry_count, max_tokens=MAX_TOKENS,
        )
        try:
            return llm.extract_json(result.text), result
        except ValueError:
            return None, result

    def feedback_for(self, work_id):
        """The Evaluator's reasons for the latest claim of this work item, read from the world state."""
        claim_key = self.world.latest_claim_key(work_id)
        verdicts = self.world.verdicts_for(claim_key) if claim_key else []
        reasons = verdicts[-1]["reasons"] if verdicts else []
        return claim_key, reasons


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")
