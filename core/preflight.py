"""Start-up checks shared by scripts/healthcheck.py and main.py. Never raises: every problem becomes a FAIL line.

Checks: required env vars, Nightshift (one tiny call; shows the model that answered), Zetaris (login + the ganga tables).
The two network checks run in parallel and the whole thing is bounded to about 25 seconds.
"""

from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass

import openai
import requests

from core import config, llm
from core.zetaris_client import ZetarisClient, ZetarisError, ZetarisUnreachable

DEADLINE_SECONDS = 25
CHECK_TIMEOUT_SECONDS = 8
GANGA_TABLES = ("parts", "suppliers", "purchases")
ZETARIS_START_HINT = ("Start Zetaris: open a terminal in the folder that holds Zetaris's docker-compose.yml "
                      "(on the build machine: ~/zetaris-platform) and run `docker compose up -d`, wait until the containers are healthy, "
                      "then load the data once with `python scripts/load_ganga_to_zetaris.py`.")
RUNTIME_BUSY_MESSAGE = "AI runtime busy or offline — partial results saved. Try again later."
RUNTIME_BUSY_EARLY = "AI runtime busy or offline — nothing was run. Try again later."

# (name, why). METERLESS_BASE_URL may also be given as the older NIGHTSHIFT_URL.
REQUIRED_ENV = (
    ("METERLESS_BASE_URL", "Nightshift address"), ("METERLESS_RUNTIME_TOKEN", "Nightshift token"),
    ("MODEL_ANALYST", "analyst lane"), ("MODEL_EVALUATOR", "evaluator / planner lane"),
    ("ZETARIS_USER", "Zetaris login"), ("ZETARIS_PASSWORD", "Zetaris password"),
)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str

    def line(self):
        return f"[{'PASS' if self.ok else 'FAIL'}] {self.name}: {self.detail}"


def check_env():
    missing = [n for n, _ in REQUIRED_ENV if not (config.env(n) or (n == "METERLESS_BASE_URL" and config.env("NIGHTSHIFT_URL")))]
    if missing:
        return Check("Environment variables", False, f"not set: {', '.join(missing)}. Copy .env.example to .env and fill it in "
                                                     "(or pass --env-file .env to docker run).")
    return Check("Environment variables", True, "all required variables are set (values never printed)")


def check_nightshift():
    name = "Nightshift (AI runtime)"
    try:
        result = llm.chat([{"role": "user", "content": "Reply with the single word: ok"}], config.analyst_model(),
                          agent="healthcheck", step="ping", max_tokens=300, max_attempts=2, timeout=CHECK_TIMEOUT_SECONDS,
                          expected_model=config.analyst_expected())
    except openai.AuthenticationError:
        return Check(name, False, "reachable but the token was rejected - check METERLESS_RUNTIME_TOKEN in .env")
    except openai.NotFoundError:
        return Check(name, False, f"reachable but lane/model '{config.analyst_model()}' was not found - check MODEL_ANALYST and the Nightshift lanes")
    except (openai.InternalServerError, openai.APITimeoutError):
        return Check(name, False, "AI runtime busy (5xx or timeout after retries) - try again later")
    except openai.APIConnectionError:
        return Check(name, False, f"not reachable at {config.nightshift_url()} - is the Nightshift app running?")
    except (openai.OpenAIError, RuntimeError) as exc:
        return Check(name, False, f"{type(exc).__name__}: {str(exc)[:200]}")
    pinned = "matches the expected model" if result.pin_honoured else f"expected {result.expected_model}"
    return Check(name, True, f"token valid; lane '{result.requested_model}' answered by model '{result.model}' ({pinned}), {result.latency_ms} ms")


def check_zetaris():
    name = "Zetaris (data layer)"
    zt = ZetarisClient(timeout=CHECK_TIMEOUT_SECONDS)
    try:
        if not zt.health_check():
            return Check(name, False, "SELECT 1 did not return 1")
        counts = {}
        for table in GANGA_TABLES:
            counts[table] = zt.query(f"SELECT COUNT(*) AS n FROM ganga.{table}")[0]["n"]
    except ZetarisUnreachable as exc:
        return Check(name, False, f"{exc} {ZETARIS_START_HINT}")
    except (ZetarisError, requests.RequestException, RuntimeError, KeyError, IndexError) as exc:
        text = str(exc)[:200]
        hint = " Load the data with `python scripts/load_ganga_to_zetaris.py`." if "ganga" in text.lower() or "table" in text.lower() else ""
        return Check(name, False, f"{text}{hint}")
    if not all(int(n) > 0 for n in counts.values()):
        return Check(name, False, "ganga tables are empty (" + ", ".join(f"{t}: {n}" for t, n in counts.items()) +
                                  "). Run `python scripts/load_ganga_to_zetaris.py`.")
    return Check(name, True, f"login ok; ganga tables present (" + ", ".join(f"{t}: {n} rows" for t, n in counts.items()) + ")")


def run_checks():
    """Returns a list of Check (env first). Network checks are skipped when env vars are missing."""
    env_check = check_env()
    if not env_check.ok:
        return [env_check]
    pool = ThreadPoolExecutor(max_workers=2)
    futures = {"nightshift": pool.submit(check_nightshift), "zetaris": pool.submit(check_zetaris)}
    wait(futures.values(), timeout=DEADLINE_SECONDS)
    results = [env_check]
    for key, label in (("nightshift", "Nightshift (AI runtime)"), ("zetaris", "Zetaris (data layer)")):
        f = futures[key]
        if f.done():
            try:
                results.append(f.result())
            except Exception as exc:  # a check must never crash the start-up
                results.append(Check(label, False, f"check crashed: {type(exc).__name__}: {str(exc)[:150]}"))
        else:
            results.append(Check(label, False, f"no answer within {DEADLINE_SECONDS} s"))
    pool.shutdown(wait=False, cancel_futures=True)
    return results
