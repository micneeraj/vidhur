"""Central settings. Everything secret is read from .env and never printed."""

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"{name} is not set. Copy .env.example to .env and fill it in.")
    return value


def in_docker():
    """True inside a container: VIDHUR_IN_DOCKER=1 (the Dockerfile sets it) or Docker's /.dockerenv marker."""
    flag = env("VIDHUR_IN_DOCKER")
    if flag is not None:
        return flag.strip().lower() in ("1", "true", "yes")
    return Path("/.dockerenv").exists()


def host_url(url):
    """Inside Docker, localhost means the container itself, so point it at the host machine instead.
    The alias is DOCKER_HOST_ALIAS (default host.docker.internal). Outside Docker the URL is returned unchanged."""
    if not in_docker():
        return url
    parts = urlsplit(url)
    if parts.hostname not in ("localhost", "127.0.0.1", "::1"):
        return url
    alias = env("DOCKER_HOST_ALIAS", "host.docker.internal")
    netloc = alias + (f":{parts.port}" if parts.port else "")
    return urlunsplit(parts._replace(netloc=netloc))


def nightshift_url():
    # METERLESS_BASE_URL is the documented name; NIGHTSHIFT_URL is the Day 1 skeleton name.
    url = env("METERLESS_BASE_URL") or env("NIGHTSHIFT_URL")
    if not url:
        raise RuntimeError("METERLESS_BASE_URL is not set. Copy .env.example to .env and fill it in.")
    return host_url(url)


def zetaris_url():
    return host_url(env("ZETARIS_API_URL", "http://localhost:8888/api/v1.0")).rstrip("/")


def analyst_model():
    """What to put in the request: a Nightshift lane id (e.g. vidhur-analyst) or a full model id."""
    return env("MODEL_ANALYST", required=True)


def analyst_expected():
    """The Nemotron model that must actually answer for the analyst. Defaults to MODEL_ANALYST."""
    return env("MODEL_ANALYST_EXPECTED") or analyst_model()


def evaluator_model():
    return env("MODEL_EVALUATOR", required=True)


def evaluator_expected():
    return env("MODEL_EVALUATOR_EXPECTED") or evaluator_model()


def evidence_max_age_days():
    return int(env("EVIDENCE_MAX_AGE_DAYS", "30"))
