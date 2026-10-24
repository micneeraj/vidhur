import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _tests_never_write_the_real_dashboard(monkeypatch, tmp_path):
    """Tests that run main() with fakes must never rewrite output_examples/dashboard.html or register a run."""
    from core import dashboard
    monkeypatch.setattr(dashboard, "OUT_FILE", tmp_path / "dashboard_test_output.html")
    import main as vidhur_main
    monkeypatch.setattr(vidhur_main, "PAGE_DIR", tmp_path / "page")  # nor output/evidence_trail.html
