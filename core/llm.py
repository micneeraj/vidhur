"""Meterless Nightshift client (OpenAI-compatible). All product AI calls go through here."""

import json
import re
import time
from dataclasses import dataclass

import openai
from openai import OpenAI

from core import config

TIMEOUT_SECONDS = 180
# First try + automatic retries. A pinned lane has no router fallback, and the runtime enforces a ~45 s
# deadline per request, so a long-reasoning call fails a good share of the time and a retry is the fix.
MAX_ATTEMPTS = int(config.env("LLM_MAX_ATTEMPTS", "5"))
# Network problems, and 5xx from the runtime: Nightshift can route a request to a model that has
# been retired upstream (it answers 502), and the next attempt usually gets a live route.
RETRYABLE_ERRORS = (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError)


@dataclass
class LLMResult:
    text: str
    model: str  # the model that actually answered (from the response)
    requested_model: str
    expected_model: str  # the model the pin is supposed to deliver (a lane id resolves to this)
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int

    @property
    def pin_honoured(self):
        return self.model == self.expected_model


def _client():
    return OpenAI(
        base_url=config.nightshift_url(),
        api_key=config.env("METERLESS_RUNTIME_TOKEN", required=True),
        timeout=TIMEOUT_SECONDS,
        max_retries=0,  # retries are handled below so they are visible
    )


def chat(messages, model, agent="llm", step="chat", trace=None, max_tokens=4000, temperature=0.2, retry_count=0,
         expected_model=None):
    """Send chat messages to Nightshift. Retries automatically on network errors and runtime 5xx errors.

    Logs the agent, requested model, answering model, tokens and latency to the trace.
    """
    client = _client()
    started = time.monotonic()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, temperature=temperature
            )
            break
        except RETRYABLE_ERRORS as exc:
            if trace is not None:
                trace.event(agent, f"llm:{step}:error", output_summary=f"{type(exc).__name__}: {str(exc)[:300]}",
                            model=None, decision="RETRY" if attempt < MAX_ATTEMPTS else "GIVE_UP",
                            retry_count=retry_count, requested_model=model, requested_lane=model, answered_model=None,
                            llm_attempt=attempt)
            if attempt == MAX_ATTEMPTS:
                raise
            time.sleep(2)

    latency_ms = int((time.monotonic() - started) * 1000)
    usage = response.usage
    result = LLMResult(
        text=(response.choices[0].message.content or "").strip(),
        model=response.model or model,
        requested_model=model,
        expected_model=expected_model or model,
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        latency_ms=latency_ms,
    )
    if trace is not None:
        trace.event(
            agent,
            f"llm:{step}",
            input_summary=messages[-1]["content"],
            output_summary=result.text,
            model=result.model,
            retry_count=retry_count,
            requested_model=result.requested_model,
            requested_lane=result.requested_model,
            answered_model=result.model,
            expected_model=result.expected_model,
            pin_honoured=result.pin_honoured,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            latency_ms=result.latency_ms,
        )
    return result


def ask_llm(prompt, model=None):
    """Simple one-prompt helper. Returns (reply, model_that_answered)."""
    result = chat([{"role": "user", "content": prompt}], model or config.analyst_model(), agent="ask_llm",
                  expected_model=None if model else config.analyst_expected())
    return result.text, result.model


def extract_json(text):
    """Pull the last JSON object out of a model reply.

    Nemotron replies may contain reasoning text or code fences around the JSON.
    Raises ValueError if no JSON object can be parsed.
    """
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    decoder = json.JSONDecoder()
    last = None
    pos = cleaned.find("{")
    while pos != -1:
        try:
            obj, length = decoder.raw_decode(cleaned[pos:])
        except json.JSONDecodeError:
            pos = cleaned.find("{", pos + 1)
            continue
        if isinstance(obj, dict):
            last = obj
        pos = cleaned.find("{", pos + length)
    if last is None:
        raise ValueError("No JSON object found in model reply")
    return last
