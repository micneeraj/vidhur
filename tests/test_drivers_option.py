"""--drivers (with --demo): parsing, subset selection, default unchanged, and 'not run' shown as such. No network, no model."""

import pytest

import main
from core import dashboard, demo, unverified
from core.brief import build_brief, console_summary
from core.evidence_page import build_evidence_page
from core.ganga_data import Target
from core.shared_state import RunWorld


def target(pid, country, spend, cat="cat"):
    return Target(pid, f"Part {pid}", cat, country, "Sup", "2026-Q3", spend)


TARGETS = [target("P001", "Taiwan", 1000.0), target("P002", "South Korea", 500.0, cat="HBM memory"),
           target("P006", "Germany", 400.0), target("P003", "Japan", 100.0)]
FX = {"EUR", "JPY"}  # Korea has no ECB data, so the memory part is D3 and Germany is D2


def ids(plan, key="delegations"):
    return [d["work_id"] for d in plan[key]]


# ---- parsing -------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,want", [("D3", ["D3"]), ("d1", ["D1"]), ("D1,D3", ["D1", "D3"]), ("D3, D1", ["D1", "D3"]), ("D2,D2", ["D2"])])
def test_parse_drivers(text, want):
    assert demo.parse_drivers(text) == want


@pytest.mark.parametrize("text", ["", "D4", "D1,X", "D1,,D3", "all"])
def test_parse_drivers_rejects_bad_ids(text):
    with pytest.raises(ValueError):
        demo.parse_drivers(text)


def test_cli_parses_and_requires_demo(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["main.py", "--demo", "--drivers", "D1,D3"])
    assert main.parse_args().drivers == ["D1", "D3"]
    monkeypatch.setattr("sys.argv", ["main.py", "--demo"])
    assert main.parse_args().drivers is None
    for argv in (["main.py", "--drivers", "D3"], ["main.py", "--demo", "--drivers", "D9"]):
        monkeypatch.setattr("sys.argv", argv)
        with pytest.raises(SystemExit):
            main.parse_args()


# ---- subset selection ----------------------------------------------------------------------------
def test_default_plan_is_unchanged_and_has_no_not_run():
    plan = demo.demo_plan(TARGETS, FX)
    assert ids(plan) == ["D1", "D2", "D3"] and "not_run" not in plan
    assert demo.demo_plan(TARGETS, FX, drivers=None) == plan


def test_d3_only_keeps_the_same_part_and_leaves_the_others_not_run():
    full, sub = demo.demo_plan(TARGETS, FX), demo.demo_plan(TARGETS, FX, drivers=["D3"])
    assert ids(sub) == ["D3"] and ids(sub, "not_run") == ["D1", "D2"]
    assert sub["delegations"][0] == full["delegations"][2]  # D3 is the same memory part as in a full demo run


def test_comma_list_runs_only_those():
    sub = demo.demo_plan(TARGETS, FX, drivers=["D1", "D3"])
    assert ids(sub) == ["D1", "D3"] and ids(sub, "not_run") == ["D2"]


def test_unavailable_driver_gives_an_empty_run_list():
    assert demo.demo_plan(TARGETS[:1], FX, drivers=["D3"])["delegations"] == []


# ---- not run is shown as not run -----------------------------------------------------------------
def _world(tmp_path):
    world = RunWorld("t2", path=tmp_path / "w.json")
    world.start_run({"run_id": "t2", "demo": True, "drivers": ["D3"]})
    world.record_plan(demo.demo_plan(TARGETS, FX, drivers=["D3"]), [])
    world.write_error("D3", "APIConnectionError: down")
    return world


def test_not_run_is_neither_zero_nor_could_not_verify(tmp_path):
    report = _world(tmp_path).report()
    assert [d["work_id"] for d in unverified.not_run(report)] == ["D1", "D2"]
    assert [u["work_id"] for u in unverified.collect(report)] == ["D3"]  # only the driver that actually ran
    brief = build_brief(report, _world(tmp_path), "logs/x.jsonl", False, {"llm_ok": 0, "llm_fail": 0}, False, False, "2026-10-24")
    assert unverified.NOT_RUN in brief and "## Not run in this run" in brief
    assert "| D1 |" in brief and "| D2 |" in brief
    assert "[NOT RUN] D1" in console_summary(report) and "[COULD NOT VERIFY] D1" not in console_summary(report)
    assert "D1 geopolitics: " + unverified.NOT_RUN in console_summary(report)


def test_dashboard_and_evidence_page_say_not_run(tmp_path):
    world = _world(tmp_path)
    report = world.report()
    html = build_evidence_page(report, world, {}, {"llm_ok": 0, "llm_fail": 0}, "2026-10-24", "logs/x.jsonl")
    assert unverified.NOT_RUN in html
    run = {"id": "r", "label": "x", "report": report, "world": world, "trail": "", "models": []}
    assert unverified.NOT_RUN in dashboard._run_section(run)


def test_full_run_report_has_no_not_run(tmp_path):
    world = RunWorld("t3", path=tmp_path / "w.json")
    world.start_run({"run_id": "t3"})
    world.record_plan(demo.demo_plan(TARGETS, FX), [])
    report = world.report()
    assert unverified.not_run(report) == []
    brief = build_brief(report, world, "logs/x.jsonl", False, {"llm_ok": 0, "llm_fail": 0})
    assert unverified.NOT_RUN not in brief and "Not run in this run" not in brief
