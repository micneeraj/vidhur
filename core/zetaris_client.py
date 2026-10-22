"""Zetaris REST client. Ganga's data is read through Lightning SQL, one statement per request."""

import time
import uuid

import requests

from core import config

REQUEST_TIMEOUT = 120


class ZetarisError(RuntimeError):
    pass


class ZetarisClient:
    def __init__(self, trace=None):
        self.base = config.env("ZETARIS_API_URL", "http://localhost:8888/api/v1.0").rstrip("/")
        self.org_id = config.env("ZETARIS_ORG_ID", "1")
        self.trace = trace
        self._token = None

    # -- auth -----------------------------------------------------------------
    def _login(self):
        user = config.env("ZETARIS_USER", required=True)
        password = config.env("ZETARIS_PASSWORD", required=True)
        response = requests.get(
            f"{self.base}/auth/login",
            headers={"X-Org-ID": self.org_id, "X-Request-ID": str(uuid.uuid4())},
            auth=(user, password),
            timeout=30,
        )
        if response.status_code != 200:
            raise ZetarisError(f"Zetaris login failed (HTTP {response.status_code}). Check ZETARIS_USER/PASSWORD/ORG_ID in .env.")
        self._token = response.json()["idToken"]

    def _headers(self):
        if not self._token:
            self._login()
        return {
            "X-Org-ID": self.org_id,
            "X-Request-ID": str(uuid.uuid4()),
            "Authorization": f"Bearer {self._token}",
        }

    # -- queries --------------------------------------------------------------
    def query(self, sql, limit=1000, agent="zetaris"):
        """Run ONE Lightning SQL statement. Returns a list of dicts (values are strings)."""
        statement = sql.strip().rstrip(";").strip()
        if ";" in statement:
            raise ValueError("Zetaris Lightning SQL takes one statement per request")
        payload = {"queryId": str(uuid.uuid4()), "sql": statement, "limit": limit, "source": "Generic"}

        started = time.monotonic()
        response = requests.post(f"{self.base}/sql-editor/sqls/run", headers=self._headers(), json=payload, timeout=REQUEST_TIMEOUT)
        if response.status_code == 401:  # token expired: log in again once
            self._token = None
            response = requests.post(f"{self.base}/sql-editor/sqls/run", headers=self._headers(), json=payload, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            raise ZetarisError(f"Zetaris SQL failed (HTTP {response.status_code}): {_error_line(response)}")

        body = response.json()
        columns = body.get("headers", [])
        rows = [dict(zip(columns, row)) for row in body.get("data", [])]
        if self.trace is not None:
            self.trace.event(
                agent,
                "zetaris:sql",
                input_summary=statement,
                output_summary=f"{len(rows)} rows",
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        return rows

    def health_check(self):
        rows = self.query("SELECT 1 AS one", agent="zetaris")
        return bool(rows) and rows[0].get("one") == "1"


def _error_line(response):
    try:
        message = response.json().get("msg", "")
    except ValueError:
        message = response.text
    # first meaningful line only; the full Java stack trace is noise
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    return (lines[0] if lines else "")[:300]


def reachable():
    """True if Zetaris login and SELECT 1 work (used to skip integration tests)."""
    try:
        return ZetarisClient().health_check()
    except Exception:
        return False
