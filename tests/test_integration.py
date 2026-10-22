"""Integration tests against the real Zetaris and Nightshift. Skipped automatically if either is not reachable."""

import pytest
import requests

from core import config
from core.zetaris_client import ZetarisClient, reachable as zetaris_reachable


def nightshift_reachable():
    try:
        r = requests.get(config.nightshift_url().rstrip("/") + "/models",
                         headers={"Authorization": "Bearer " + (config.env("METERLESS_RUNTIME_TOKEN") or "")}, timeout=5)
        return r.status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not zetaris_reachable(), reason="Zetaris is not reachable")
def test_zetaris_returns_ganga_data():
    zt = ZetarisClient()
    assert zt.health_check()
    rows = zt.query("SELECT COUNT(*) AS n FROM ganga.purchases")
    assert int(rows[0]["n"]) == 248
    spend = zt.query("SELECT s.country AS country, SUM(u.total_gbp) AS t FROM ganga.purchases u "
                     "JOIN ganga.parts p ON u.part_id = p.part_id JOIN ganga.suppliers s ON p.supplier_id = s.supplier_id "
                     "GROUP BY s.country ORDER BY t DESC")
    assert spend[0]["country"] == "Taiwan"


@pytest.mark.skipif(not nightshift_reachable(), reason="Nightshift runtime is not reachable")
def test_one_real_nemotron_call():
    from core.llm import chat
    result = chat([{"role": "user", "content": "Reply with the single word: ready"}], config.analyst_model(), agent="test", max_tokens=400)
    assert "nemotron" in result.model.lower()  # whichever Nemotron Nightshift routed to
    assert result.text
    assert result.completion_tokens > 0
