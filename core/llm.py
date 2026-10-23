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
# Seconds between attempts (default 2). The evaluation jobs set LLM_RETRY_WAIT_SECONDS=60 and LLM_MAX_ATTEMPTS=4
# (one try + 3 retries) so a busy 1.2.1 runtime gets time to recover.
RETRY_WAIT_SECONDS = float(config.env("LLM_RETRY_WAIT_SECONDS", "2"))
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
    receipt_id: str = None  # Nightshift receipt id if the response carries one (1.2.1 does not always)

    @property
    def pin_honoured(self):
        return self.model == self.expected_model


def _receipt_in(obj):
    """A receipt id from a response body (dict of extra fields), if any key mentions 'receipt'."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if "receipt" in str(k).lower() and v:
                return str(v) if not isinstance(v, dict) else str(v.get("id") or v)
    return None


def error_details(exc):
    """Honest facts about a failed call: HTTP status, the model the runtime says it routed to, and a receipt id if one is exposed."""
    out = {"status_code": getattr(exc, "status_code", None), "routed_model": None, "receipt_id": None}
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        detail = str(body.get("meterless_detail") or "")
        out["routed_model"] = detail.split("_empty_response")[0] or None
        out["receipt_id"] = _receipt_in(body)
    response = getattr(exc, "response", None)
    if response is not None:
        for k, v in response.headers.items():
            if "receipt" in k.lower():
                out["receipt_id"] = v
    return out


def _client(timeout=None):
    return OpenAI(
        base_url=config.nightshift_url(),
        api_key=config.env("METERLESS_RUNTIME_TOKEN", required=True),
        timeout=timeout or TIMEOUT_SECONDS,
        max_retries=0,  # retries are handled below so they are visible
    )


def chat(messages, model, agent="llm", step="chat", trace=None, max_tokens=4000, temperature=0.2, retry_count=0,
         expected_model=None, max_attempts=None, timeout=None):
    """Send chat messages to Nightshift. Retries automatically on network errors and runtime 5xx errors.

    Logs the agent, requested model, answering model, tokens and latency to the trace.
    """
    attempts = max_attempts or MAX_ATTEMPTS
    client = _client(timeout)
    started = time.monotonic()
    for attempt in range(1, attempts + 1):
        try:
            response = client.chat.completions.create(
                model=model, messages=messages, max_tokens=max_tokens, temperature=temperature
            )
            break
        except RETRYABLE_ERRORS as exc:
            if trace is not None:
                trace.event(agent, f"llm:{step}:error", output_summary=f"{type(exc).__name__}: {str(exc)[:300]}",
                            model=None, decision="RETRY" if attempt < attempts else "GIVE_UP",
                            retry_count=retry_count, requested_model=model, requested_lane=model, answered_model=None,
                            llm_attempt=attempt, **error_details(exc))
            if attempt == attempts:
                raise
            time.sleep(RETRY_WAIT_SECONDS)

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
        receipt_id=_receipt_in(getattr(response, "model_extra", None)),
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
            receipt_id=result.receipt_id,
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
