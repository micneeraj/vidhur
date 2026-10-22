"""Central settings. Everything secret is read from .env and never printed."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return value


def nightshift_url():
    # METERLESS_BASE_URL is the documented name; NIGHTSHIFT_URL is the Day 1 skeleton name.
    url = env("METERLESS_BASE_URL") or env("NIGHTSHIFT_URL")
    if not url:
        raise RuntimeError("METERLESS_BASE_URL is not set. Copy .env.example to .env and fill it in.")
    return url


def analyst_model():
    return env("MODEL_ANALYST", required=True)


def evaluator_model():
    return env("MODEL_EVALUATOR", required=True)


def evidence_max_age_days():
    return int(env("EVIDENCE_MAX_AGE_DAYS", "30"))
