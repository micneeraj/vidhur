"""Graceful failure paths with simulated outages: no network, no Docker, no real model."""

import os
import types

import openai
import pytest
import requests

try:  # the openai package builds its errors on httpx, or on httpx2 in newer releases
    import httpx
except ImportError:
    import httpx2 as httpx

import main as vidhur_main
from core import brief, config, decisions, failures, llm, preflight
from core.preflight import Check
from core.shared_state import RunWorld
from core.trace import Trace
from core.zetaris_client import ZetarisClient, ZetarisUnreachable

REQ = httpx.Request("POST", "http://127.0.0.1:8088/v1/chat/completions")


def busy():
    return openai.InternalServerError("502 provider_error", response=httpx.Response(502, request=REQ), body=None)


# ---- Docker host mapping ----------------------------------------------------------------------
def test_localhost_is_mapped_to_the_host_inside_docker(monkeypatch):
    monkeypatch.setenv("VIDHUR_IN_DOCKER", "1")
    monkeypatch.delenv("DOCKER_HOST_ALIAS", raising=False)
    assert config.host_url("http://127.0.0.1:8088/v1") == "http://host.docker.internal:8088/v1"
    assert config.host_url("http://localhost:8888/api/v1.0") == "http://host.docker.internal:8888/api/v1.0"
    assert config.host_url("https://example.com/v1") == "https://example.com/v1"  # a real host is left alone
    monkeypatch.setenv("DOCKER_HOST_ALIAS", "my-host")
    assert config.host_url("http://localhost:8888/x") == "http://my-host:8888/x"


def test_urls_are_unchanged_outside_docker(monkeypatch):
    monkeypatch.setenv("VIDHUR_IN_DOCKER", "0")
    assert config.host_url("http://127.0.0.1:8088/v1") == "http://127.0.0.1:8088/v1"


# ---- Zetaris ----------------------------------------------------------------------------------
def test_zetaris_connection_refused_becomes_a_clear_error(monkeypatch):
    def refuse(*a, **k):
        raise requests.ConnectionError("refused")
    monkeypatch.setattr(requests, "request", refuse)
    monkeypatch.setenv("ZETARIS_USER", "u")
    monkeypatch.setenv("ZETARIS_PASSWORD", "p")
    with pytest.raises(ZetarisUnreachable):
        ZetarisClient().health_check()


def test_zetaris_check_tells_the_user_to_start_it(monkeypatch):
    def refuse(self, *a, **k):
        raise ZetarisUnreachable("Zetaris is not reachable at http://localhost:8888/api/v1.0 (ConnectionError).")
    monkeypatch.setattr(ZetarisClient, "health_check", refuse)
    check = preflight.check_zetaris()
    assert not check.ok and "docker compose up -d" in check.detail


def test_main_exits_cleanly_when_zetaris_is_down(monkeypatch, tmp_path):
    monkeypatch.setattr(vidhur_main, "run_checks", lambda: [Check("Zetaris", True, "ok")])
    monkeypatch.setattr(vidhur_main, "Trace", lambda: Trace(log_dir=tmp_path, quiet=True))
    monkeypatch.setattr(vidhur_main, "RunWorld", lambda run_id: types.SimpleNamespace(path="x"))
    monkeypatch.setattr(vidhur_main.sys, "argv", ["main.py"])

    def down(self):
        raise ZetarisUnreachable("Zetaris is not reachable.")
    monkeypatch.setattr(ZetarisClient, "health_check", down)
    with pytest.raises(SystemExit) as exit_info:
        vidhur_main.main()
    assert "docker compose up -d" in str(exit_info.value)  # a message, not a traceback


# ---- health checks ----------------------------------------------------------------------------
def test_env_check_names_missing_variables_but_never_values(monkeypatch):
    for name, _ in preflight.REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("NIGHTSHIFT_URL", raising=False)
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "super-secret-value")
    monkeypatch.setattr(config, "env", lambda name, default=None, required=False: os.getenv(name, default))
    check = preflight.check_env()
    assert not check.ok and "METERLESS_BASE_URL" in check.detail and "super-secret-value" not in check.detail


@pytest.mark.parametrize("error, expect", [
    (busy(), "busy"),
    (openai.APIConnectionError(request=REQ), "not reachable"),
    (openai.AuthenticationError("bad key", response=httpx.Response(401, request=REQ), body=None), "token was rejected"),
])
def test_nightshift_check_reports_each_failure_in_plain_words(monkeypatch, error, expect):
    monkeypatch.setattr(config, "analyst_model", lambda: "vidhur-analyst")
    monkeypatch.setattr(config, "nightshift_url", lambda: "http://127.0.0.1:8088/v1")

    def fail(*a, **k):
        raise error
    monkeypatch.setattr(llm, "chat", fail)
    check = preflight.check_nightshift()
    assert not check.ok and expect in check.detail


def test_nightshift_check_shows_the_answering_model(monkeypatch):
    monkeypatch.setattr(config, "analyst_model", lambda: "vidhur-analyst")
    monkeypatch.setattr(config, "analyst_expected", lambda: "nvidia/nemotron")
    monkeypatch.setattr(llm, "chat", lambda *a, **k: llm.LLMResult("ok", "nvidia/nemotron", "vidhur-analyst", "nvidia/nemotron", 1, 1, 120))
    check = preflight.check_nightshift()
    assert check.ok and "nvidia/nemotron" in check.detail


def test_main_stops_early_when_a_required_check_fails(monkeypatch, capsys):
    monkeypatch.setattr(vidhur_main, "run_checks", lambda: [Check("Nightshift (AI runtime)", False, "AI runtime busy (5xx)")])
    monkeypatch.setattr(vidhur_main.sys, "argv", ["main.py"])
    with pytest.raises(SystemExit) as exit_info:
        vidhur_main.main()
    out = capsys.readouterr().out
    assert exit_info.value.code == vidhur_main.EXIT_STOPPED_EARLY and "[FAIL] Nightshift" in out and "Try again later" in out


# ---- a runtime outage mid-run ------------------------------------------------------------------
def test_planner_outage_gives_a_partial_brief_and_nonzero_exit(monkeypatch, tmp_path, capsys):
    target = types.SimpleNamespace(part_id="P001", part_name="GPU", category="c", country="Taiwan", supplier_name="Formosa",
                                   quarter="2026Q3", quarterly_spend_gbp=1000.0)
    monkeypatch.setattr(vidhur_main, "run_checks", lambda: [Check("all", True, "ok")])
    monkeypatch.setattr(vidhur_main, "Trace", lambda: Trace(log_dir=tmp_path, quiet=True))
    monkeypatch.setattr(vidhur_main, "RunWorld", lambda run_id: RunWorld(run_id, path=tmp_path / "world.json"))
    monkeypatch.setattr(vidhur_main, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setattr(vidhur_main, "INPUT_DIR", tmp_path / "in")
    monkeypatch.setattr(vidhur_main, "ZetarisClient", lambda trace=None: types.SimpleNamespace(health_check=lambda: True))
    monkeypatch.setattr(vidhur_main, "top_targets", lambda zt, n: [target])
    monkeypatch.setattr(vidhur_main, "part_country_catalog", lambda zt: {})
    monkeypatch.setattr(vidhur_main, "GeopoliticsAnalyst", lambda *a, **k: object())
    monkeypatch.setattr(vidhur_main, "CurrencyAnalyst", lambda *a, **k: object())
    monkeypatch.setattr(vidhur_main, "Evaluator", lambda *a, **k: object())

    class DownPlanner:
        def __init__(self, trace, world):
            pass

        def plan(self, targets):
            raise busy()
    monkeypatch.setattr(vidhur_main, "Planner", DownPlanner)
    monkeypatch.setattr(vidhur_main.sys, "argv", ["main.py"])
    with pytest.raises(SystemExit) as exit_info:
        vidhur_main.main()
    assert exit_info.value.code == vidhur_main.EXIT_PARTIAL
    out = capsys.readouterr().out
    assert "AI runtime busy or offline — partial results saved. Try again later." in out
    text = next((tmp_path / "out").glob("brief_*.md")).read_text(encoding="utf-8")
    assert "Agents that could not run" in text and "Planner" in text and "PARTIAL RESULTS" in text
    assert (tmp_path / "world.json").exists()  # the world state was saved


# ---- the brief's honest labels ------------------------------------------------------------------
def test_failures_are_collected_from_the_world_report():
    report = {"run": {"stopped": [{"agent": "Planner", "stage": "review", "error": "502"}]},
              "items": [{"delegation": {"work_id": "D1", "analyst": "currency", "part_id": "P2", "status": "NOT_ASSESSED", "error": "timeout"}}],
              "decisions": {"D2": {"outcome": {"status": "NOT_CHALLENGED", "error": "502"}},
                            "D3": {"outcome": {"status": "NOT_DECIDED", "error": "502"}},
                            "D4": {"outcome": {"status": "RECOMMENDED"}}}}
    assert [f["agent"] for f in failures.collect(report)] == ["Planner", "currency analyst / Evaluator", "Red Team", "Decision Maker"]
    assert failures.collect({"run": {}, "items": [], "decisions": {}}) == []
    assert failures.brief_section({"run": {}, "items": []}) == []


def _decision_report(outcome):
    version = {"version": 1, "kind": "HEDGE", "share_pct": 50, "rationale": "because", "cost": {k: 0 for k in decisions.SCENARIOS}}
    claim = types.SimpleNamespace(part_name="GPU Chip", supplier_country="Taiwan")
    impact = {"baseline_quarterly_spend_gbp": 100000.0, "worst_gbp": 8000.0, "likely_gbp": 5000.0, "best_gbp": 2000.0}
    item = {"delegation": {"work_id": "D1", "status": "PASS", "action": "run"}, "attempts": [{"claim": claim}], "impact": impact}
    return {"decisions": {"D1": {"versions": [version], "objections": [], "outcome": outcome}}}, [item]


def test_unreviewed_decision_uses_the_required_wording():
    report, passed = _decision_report({"status": "NOT_CHALLENGED", "error": "InternalServerError: 502", "rounds": 0})
    text = "\n".join(brief._decisions_section(report, passed))
    assert "Could not verify: the Red Team review did not run (AI runtime busy). Treat this recommendation as unreviewed." in text
    assert "NOT challenged" not in text and "Assumptions: see data/decision_assumptions.csv" in text


def test_reviewed_decision_without_objections_has_different_wording():
    report, passed = _decision_report({"status": "RECOMMENDED", "rounds": 0})
    text = "\n".join(brief._decisions_section(report, passed))
    assert "Red Team reviewed this decision and raised no high or medium objections" in text and "could not run" not in text


# ---- assumptions live in the CSV -----------------------------------------------------------------
def test_premiums_come_from_the_csv(tmp_path):
    csv_file = tmp_path / "a.csv"
    csv_file.write_text("option,premium_pct,note\nNO_ACTION,0,x\nHEDGE,9,x\nLOCK_PRICE,8,x\nDUAL_SOURCE,7,x\n", encoding="utf-8")
    assert decisions.load_premiums(csv_file)["HEDGE"] == 9.0
    csv_file.write_text("option,premium_pct,note\nHEDGE,9,x\n", encoding="utf-8")
    with pytest.raises(ValueError):
        decisions.load_premiums(csv_file)
    shipped = decisions.load_premiums()
    assert (shipped["HEDGE"], shipped["DUAL_SOURCE"]) == (1.5, 3.0)
    assert "LOCK_PRICE" not in shipped  # no documented price-lock premium: its cost needs a supplier quote
    notes = decisions.ASSUMPTIONS_FILE.read_text(encoding="utf-8").splitlines()[2:]
    assert all("illustrative assumption" in line.lower() and "not a market quote" in line for line in notes)


def test_llm_trace_keeps_long_replies_but_other_steps_stay_short(tmp_path):
    import json
    from core.trace import LLM_REPLY_LIMIT, Trace
    trace = Trace(log_dir=tmp_path, quiet=True)
    reply = "x" * 5000
    trace.event("a", "llm:claim", output_summary=reply)
    trace.event("a", "other", output_summary=reply)
    trace.event("a", "llm:claim", output_summary="y" * 20000)
    rows = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["output_summary"] == reply and len(rows[1]["output_summary"]) == 400
    assert len(rows[2]["output_summary"]) == LLM_REPLY_LIMIT == 8000


# ---- objections raised but the challenge could not finish (run d9decb7d, D1) --------------------------------
def _d1_unresolved(status="CHALLENGE_UNRESOLVED"):
    objections = [{"id": "O1", "severity": "high"}, {"id": "O2", "severity": "high"}, {"id": "O3", "severity": "medium"}]
    return {"versions": [{}], "objections": objections, "outcome": {"status": status, "error": "APITimeoutError: Request timed out.", "rounds": 0}}


def test_unresolved_challenge_is_told_apart_from_a_red_team_that_could_not_run():
    for status in ("CHALLENGE_UNRESOLVED", "NOT_CHALLENGED"):  # NOT_CHALLENGED + objections is how older saved runs recorded it
        d = _d1_unresolved(status)
        assert decisions.is_unresolved(d)
        assert decisions.unresolved_label(d).startswith("Red Team raised 3 objections (2 high, 1 medium); the Decision Maker's response timed out, so the challenge is unresolved.")
    assert not decisions.is_unresolved({"versions": [{}], "objections": [], "outcome": {"status": "NOT_CHALLENGED", "error": "502"}})
    assert not decisions.is_unresolved({"versions": [{}], "objections": [{}], "outcome": {"status": "RECOMMENDED"}})


def test_failure_report_names_the_decision_maker_when_objections_were_raised():
    report = {"run": {}, "items": [], "decisions": {"D1": _d1_unresolved(), "D3": {"versions": [{}], "objections": [], "outcome": {"status": "NOT_CHALLENGED", "error": "budget"}}}}
    found = failures.collect(report)
    assert [(f["agent"], f["where"]) for f in found] == [("Decision Maker", "D1"), ("Red Team", "D3")]
    assert "3 Red Team objection(s)" in found[0]["reason"]


def test_brief_shows_the_unresolved_challenge_not_a_red_team_that_did_not_run():
    report, passed = _decision_report({"status": "NOT_CHALLENGED", "error": "APITimeoutError: Request timed out.", "rounds": 0})
    report["decisions"]["D1"]["objections"] = [{"id": f"O{i}", "severity": s, "type": "weak_evidence", "evidence_refs": ["S1"], "text": "t", "response": {}, "status": "open"}
                                               for i, s in enumerate(("high", "high", "medium"), 1)]
    text = "\n".join(brief._decisions_section(report, passed))
    assert "Proposed, challenge UNRESOLVED" in text and "Red Team raised 3 objections (2 high, 1 medium)" in text
    assert "the Red Team review did not run" not in text and "Proposed, UNREVIEWED" not in text


def test_run_decisions_records_objections_already_raised_when_the_response_call_fails(tmp_path, monkeypatch):
    import openai
    from agents import decision_maker
    from core.shared_state import RunWorld
    world = RunWorld("t", path=tmp_path / "w.json")
    world.write_decision("D1", {"version": 1, "kind": "NO_ACTION", "share_pct": 0, "rationale": "r", "cost": {}})
    for i, sev in enumerate(("high", "high", "medium"), 1):
        world.write_objection("D1", {"id": f"O{i}", "severity": sev, "type": "weak_evidence", "evidence_refs": ["S1"], "text": "t", "response": {}}, 1)

    def boom(*a, **k):
        raise openai.APITimeoutError(request=None)
    monkeypatch.setattr(decision_maker, "build_evidence", boom)
    item = {"delegation": {"work_id": "D1", "action": "run", "status": "PASS"}, "impact": {"baseline_quarterly_spend_gbp": 1.0, "confidence": "Low"}}
    decision_maker.run_decisions({"items": [item]}, world, [], types.SimpleNamespace(event=lambda *a, **k: None, target=lambda label: __import__("contextlib").nullcontext()))
    outcome = world.decision_report()["D1"]["outcome"]
    assert (outcome["status"], outcome["objection_count"]) == ("CHALLENGE_UNRESOLVED", 3)
