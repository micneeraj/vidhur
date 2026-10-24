"""Red Team objections, the Decision Maker's responses, and the challenge loop. Model replies are scripted: no network."""

import pytest

from agents.decision_maker import DecisionMaker, apply_responses, challenge_decision, handling, make_decision, parse_proposal
from core import decisions
from core.red_team import RedTeam, _number_tokens, evidence_block, known_ids, parse_objections, parse_statuses
from core.shared_state import RunWorld
from core.trace import Trace

IMPACT = {"worst_gbp": 8000.0, "likely_gbp": 5000.0, "best_gbp": 2000.0, "baseline_quarterly_spend_gbp": 100000.0,
          "confidence": "Low"}
EV = {"work_id": "D1", "analyst": "geopolitics", "part_id": "P001", "part_name": "GPU Chip", "claim": "Export curbs raise GPU costs.",
      "direction": "cost_up", "pct_low": 2.0, "pct_high": 8.0, "confidence": "Low", "strength": "weak", "retries": 1,
      "strength_line": "2 independent sources, 1 Tier 1", "spend_gbp": 100000.0, "impact": IMPACT,
      "sources": [{"id": "S1", "publisher": "Reuters", "tier": 1, "date": "2026-10-20", "title": "Taiwan curbs chip exports"},
                  {"id": "S2", "publisher": "Blog", "tier": 3, "date": "2026-10-19", "title": "Chips gossip"}],
      "concentration": {"total_spend_gbp": 400000, "part_share_pct": 25.0, "country": "Taiwan", "country_share_pct": 45.5,
                        "supplier": "Formosa", "supplier_share_pct": 25.0}}


def ids_numbers(kind="HEDGE", share=100):
    decision = make_decision(100000.0, IMPACT, kind, share, "r", 1, "model")
    rows = decisions.compare_options(100000.0, IMPACT, (kind, share))
    return known_ids(EV, rows), _number_tokens(evidence_block(EV, rows, decision))


GOOD = {"type": "forecast_wrong", "severity": "high", "objection": "Single Low-confidence claim.", "evidence_refs": ["S2", "Low"]}


# ---- GBP maths is Python ----------------------------------------------------------------------
def test_option_cost_formula():
    c = decisions.option_cost(100000.0, IMPACT, "HEDGE", 50)
    assert c["premium_gbp"] == 750  # 100k * 50% * 1.5%
    assert c["worst"] == round(0.5 * 8000 + 750) and c["likely"] == round(0.5 * 5000 + 750)
    assert c["forecast_wrong"] == round(0.5 * -5000 + 750)
    assert decisions.option_cost(100000.0, IMPACT, "NO_ACTION", 0)["worst"] == 8000


def test_normalize_choice_clamps_and_rejects():
    assert decisions.normalize_choice("hedge", 250) == ("HEDGE", 100)
    assert decisions.normalize_choice("LOCK_PRICE", 10) == ("LOCK_PRICE", 100)
    assert decisions.normalize_choice("NO ACTION", 40) == ("NO_ACTION", 0)
    assert decisions.normalize_choice("buy a boat", 10) is None
    assert decisions.normalize_choice("HEDGE", "lots") is None


def test_parse_proposal():
    assert parse_proposal({"kind": "HEDGE", "share_pct": 30, "rationale": "x"}) == ("HEDGE", 30, "x")
    assert parse_proposal({"kind": "HEDGE", "share_pct": 30}) is None
    assert parse_proposal(None) is None


# ---- objection JSON parsing and validation ----------------------------------------------------
def test_valid_objection_is_kept():
    ids, nums = ids_numbers()
    valid, rejected = parse_objections({"objections": [GOOD]}, ids, nums)
    assert len(valid) == 1 and not rejected and valid[0]["evidence_refs"] == ["S2"]  # "Low" is neither an id nor a number


def test_objection_without_evidence_reference_is_rejected():
    ids, nums = ids_numbers()
    vague = {**GOOD, "evidence_refs": []}
    wrong = {**GOOD, "evidence_refs": ["the vibes", "S99"]}
    valid, rejected = parse_objections({"objections": [vague, wrong]}, ids, nums)
    assert valid == [] and len(rejected) == 2


def test_number_reference_must_appear_in_the_evidence():
    ids, nums = ids_numbers()
    ok, _ = parse_objections({"objections": [{**GOOD, "evidence_refs": ["£5,000"]}]}, ids, nums)  # the likely case
    bad, rej = parse_objections({"objections": [{**GOOD, "evidence_refs": ["£987,654"]}]}, ids, nums)
    assert len(ok) == 1 and bad == [] and rej


def test_unknown_type_severity_or_garbage_is_rejected():
    ids, nums = ids_numbers()
    items = ["nope", {**GOOD, "type": "vibes"}, {**GOOD, "severity": "catastrophic"}, {**GOOD, "objection": ""}]
    valid, rejected = parse_objections({"objections": items}, ids, nums)
    assert valid == [] and len(rejected) == 3  # at most 3 objections are read
    assert parse_objections(None, ids, nums)[0] == [] and parse_objections({"objections": "x"}, ids, nums)[0] == []


def test_statuses_default_to_standing():
    s = parse_statuses({"statuses": [{"objection_id": "O1", "status": "resolved", "reason": "fixed"}]}, ["O1", "O2"])
    assert s["O1"]["status"] == "resolved" and s["O2"]["status"] == "stands"
    assert parse_statuses({"statuses": [{"objection_id": "O1", "status": "resolved", "reason": ""}]}, ["O1"])["O1"]["status"] == "stands"


class ScriptedMixin:
    """Replaces the model call: replies come from a script keyed by step name, in order."""

    def _setup(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []

    def _ask(self, prompt, step, retry_count=0):
        self.calls.append(step)
        return self.script[step].pop(0)


class FakeRT(ScriptedMixin, RedTeam):
    def __init__(self, trace, script):
        RedTeam.__init__(self, trace, model="vidhur-analyst")
        self._setup(script)


class FakeDM(ScriptedMixin, DecisionMaker):
    def __init__(self, trace, script):
        DecisionMaker.__init__(self, trace, model="vidhur-analyst")
        self._setup(script)


@pytest.fixture
def env(tmp_path):
    trace = Trace(log_dir=tmp_path, quiet=True)
    world = RunWorld("t1", path=tmp_path / "world.json")
    world.set_delegation_status("D1", "PASS", "test")
    return trace, world


def test_red_team_reasks_once_when_no_objection_has_a_reference(env):
    trace, _ = env
    rt = FakeRT(trace, {"challenge": [{"objections": [{**GOOD, "evidence_refs": []}]}, {"objections": [GOOD]}]})
    dec = make_decision(100000.0, IMPACT, "HEDGE", 100, "r", 1, "model")
    out = rt.challenge(EV, decisions.compare_options(100000.0, IMPACT, ("HEDGE", 100)), dec)
    assert rt.calls == ["challenge", "challenge"] and len(out) == 1


def test_red_team_gives_up_after_one_reask(env):
    trace, _ = env
    rt = FakeRT(trace, {"challenge": [{"objections": []}, {"objections": []}, {"objections": [GOOD]}]})
    dec = make_decision(100000.0, IMPACT, "HEDGE", 100, "r", 1, "model")
    assert rt.challenge(EV, decisions.compare_options(100000.0, IMPACT, ("HEDGE", 100)), dec) == [] and rt.calls == ["challenge", "challenge"]


# ---- revise / defend handling -----------------------------------------------------------------
OBJ = {"id": "O1", "severity": "high", "type": "forecast_wrong", "text": "t", "evidence_refs": ["S2"], "suggested_change": ""}


def test_revise_recalculates_gbp_in_python_and_ignores_model_numbers(env):
    first = make_decision(100000.0, IMPACT, "HEDGE", 100, "r", 1, "model")
    reply = {"responses": [{"objection_id": "O1", "action": "REVISE", "reason": "smaller hedge", "evidence_refs": ["S2"]}],
             "revised_decision": {"kind": "HEDGE", "share_pct": 30, "rationale": "cut to 30%", "cost": {"worst": 1, "likely": 1},
                                  "likely_gbp": 1}}
    responses, new = apply_responses(EV, first, [OBJ], reply)
    assert responses["O1"]["action"] == "REVISE" and new["version"] == 2 and new["source"] == "revision"
    expected = decisions.option_cost(100000.0, IMPACT, "HEDGE", 30)
    assert new["cost"] == {k: expected[k] for k in decisions.SCENARIOS} and new["cost"]["worst"] != 1
    assert new["premium_gbp"] == 450


def test_defend_needs_evidence_and_revise_needs_a_different_decision():
    first = make_decision(100000.0, IMPACT, "HEDGE", 100, "r", 1, "model")
    no_refs = {"responses": [{"objection_id": "O1", "action": "DEFEND", "reason": "trust me"}]}
    assert apply_responses(EV, first, [OBJ], no_refs)[0]["O1"]["action"] == "NONE"
    defend = {"responses": [{"objection_id": "O1", "action": "DEFEND", "reason": "Reuters is Tier 1", "evidence_refs": ["S1"]}]}
    responses, new = apply_responses(EV, first, [OBJ], defend)
    assert responses["O1"]["action"] == "DEFEND" and new is None
    same = {"responses": [{"objection_id": "O1", "action": "REVISE", "reason": "r"}],
            "revised_decision": {"kind": "HEDGE", "share_pct": 100, "rationale": "same"}}
    assert apply_responses(EV, first, [OBJ], same) == ({"O1": {"action": "NONE", "reason": "invalid: REVISE without a usable, different revised decision", "evidence_refs": []}}, None)
    assert apply_responses(EV, first, [OBJ], None)[0]["O1"]["action"] == "NONE"


def test_a_revision_needs_a_written_reason_tied_to_evidence_or_a_calculation():
    first = make_decision(100000.0, IMPACT, "HEDGE", 100, "r", 1, "model")
    revised = {"kind": "HEDGE", "share_pct": 30, "rationale": "cut to 30%"}

    def attempt(**response):
        reply = {"responses": [{"objection_id": "O1", "action": "REVISE", **response}], "revised_decision": revised}
        return apply_responses(EV, first, [OBJ], reply)

    ok, new = attempt(reason="Only a Tier 3 blog backs the rise (S2).", evidence_refs=["S2"])
    assert ok["O1"]["action"] == "REVISE" and new["share_pct"] == 30
    calc_ok, new = attempt(reason="Premium on the protected spend is 450.", evidence_refs=["100000"])  # a number from the calculation
    assert calc_ok["O1"]["action"] == "REVISE" and new is not None
    for bad in ({"reason": "", "evidence_refs": ["S2"]},          # no written reason
                {"reason": "smaller is safer"},                      # reason tied to nothing
                {"reason": "smaller is safer", "evidence_refs": ["S99", "trust me"]}):  # refs that do not exist
        rejected, new = attempt(**bad)
        assert rejected["O1"]["action"] == "NONE" and new is None, bad
        assert "written reason tied to evidence or a calculation" in rejected["O1"]["reason"]
    no_rationale = {"responses": [{"objection_id": "O1", "action": "REVISE", "reason": "r", "evidence_refs": ["S2"]}],
                    "revised_decision": {"kind": "HEDGE", "share_pct": 30}}
    assert apply_responses(EV, first, [OBJ], no_rationale)[1] is None


def test_handling_labels():
    base = {"severity": "high", "status": "resolved", "response": {"action": "REVISE"}}
    assert handling(base) == "REVISED"
    assert handling({**base, "response": {"action": "DEFEND"}}) == "DEFENDED"
    assert handling({**base, "status": "standing"}) == "CONTESTED"
    assert handling({**base, "status": "standing", "severity": "medium"}) == "OPEN"
    assert handling({**base, "severity": "low"}) == "NOTED"


# ---- the loop ---------------------------------------------------------------------------------
PROPOSE_AGGRESSIVE = {"kind": "HEDGE", "share_pct": 100, "rationale": "Hedge it all."}
REVISE_30 = {"responses": [{"objection_id": "O1", "action": "REVISE", "reason": "cut because S2 is a Tier 3 blog", "evidence_refs": ["S2"]}],
             "revised_decision": {"kind": "HEDGE", "share_pct": 30, "rationale": "30% only"}}
DEFEND = {"responses": [{"objection_id": "O1", "action": "DEFEND", "reason": "Reuters Tier 1 source", "evidence_refs": ["S1"]}]}
RESOLVED = {"statuses": [{"objection_id": "O1", "status": "resolved", "reason": "ok now"}], "new_objections": []}
STANDS = {"statuses": [{"objection_id": "O1", "status": "stands", "reason": "still too much"}], "new_objections": []}


def run(env, dm_script, rt_script, aggressive=True):
    trace, world = env
    dm, rt = FakeDM(trace, dm_script), FakeRT(trace, rt_script)
    return challenge_decision(EV, dm, rt, world, trace, aggressive=aggressive), dm, rt, world


def test_revise_then_resolved_in_one_round(env):
    outcome, dm, rt, world = run(env, {"propose": [PROPOSE_AGGRESSIVE], "respond": [REVISE_30]},
                                 {"challenge": [{"objections": [GOOD]}], "review": [RESOLVED]})
    assert outcome["status"] == "RECOMMENDED" and outcome["rounds"] == 1 and outcome["changed"]
    report = world.decision_report()["D1"]
    assert [v["share_pct"] for v in report["versions"]] == [100, 30]  # before and after are both in the world state
    assert report["objections"][0]["handling"] == "REVISED" and report["outcome"]["status"] == "RECOMMENDED"


def test_defended_decision_is_unchanged(env):
    outcome, *_ = run(env, {"propose": [PROPOSE_AGGRESSIVE], "respond": [DEFEND]},
                      {"challenge": [{"objections": [GOOD]}], "review": [RESOLVED]})
    assert outcome["status"] == "RECOMMENDED" and not outcome["changed"] and outcome["final_version"] == 1


def test_max_two_rounds_then_contested(env):
    outcome, dm, rt, world = run(env, {"propose": [PROPOSE_AGGRESSIVE], "respond": [DEFEND, DEFEND, DEFEND]},
                                 {"challenge": [{"objections": [GOOD]}], "review": [STANDS], "final-review": [STANDS]})
    assert outcome["status"] == "CONTESTED" and outcome["rounds"] == 2
    assert dm.calls.count("respond") == 2 and rt.calls == ["challenge", "review", "final-review"]  # never a third round
    assert world.decision_report()["D1"]["objections"][0]["handling"] == "CONTESTED"


def test_medium_objection_left_open_does_not_contest(env):
    medium = {**GOOD, "severity": "medium"}
    outcome, *_ = run(env, {"propose": [PROPOSE_AGGRESSIVE], "respond": [DEFEND, DEFEND]},
                      {"challenge": [{"objections": [medium]}], "review": [STANDS], "final-review": [STANDS]})
    assert outcome["status"] == "RECOMMENDED"


def test_low_objection_needs_no_response(env):
    low = {**GOOD, "severity": "low"}
    outcome, dm, rt, _ = run(env, {"propose": [PROPOSE_AGGRESSIVE], "respond": []}, {"challenge": [{"objections": [low]}]})
    assert outcome["rounds"] == 0 and dm.calls == ["propose"] and outcome["status"] == "RECOMMENDED"


def test_no_usable_objections_is_reported_as_unchallenged(env):
    outcome, *_ = run(env, {"propose": [PROPOSE_AGGRESSIVE]}, {"challenge": [{"objections": []}, {"objections": []}]})
    assert outcome["status"] == "UNCHALLENGED"


def test_revision_that_creates_a_new_high_objection_gets_a_second_round(env):
    new_issue = {"statuses": [{"objection_id": "O1", "status": "resolved", "reason": "ok"}],
                 "new_objections": [{**GOOD, "objection": "Now too exposed.", "evidence_refs": ["S1"]}]}
    second = {"responses": [{"objection_id": "O2", "action": "DEFEND", "reason": "Reuters Tier 1", "evidence_refs": ["S1"]}]}
    outcome, dm, rt, _ = run(env, {"propose": [PROPOSE_AGGRESSIVE], "respond": [REVISE_30, second]},
                             {"challenge": [{"objections": [GOOD]}], "review": [new_issue],
                              "final-review": [{"statuses": [{"objection_id": "O2", "status": "resolved", "reason": "fine"}]}]})
    assert outcome["rounds"] == 2 and outcome["status"] == "RECOMMENDED" and dm.calls.count("respond") == 2


def test_unusable_proposal_falls_back_to_monitor(env):
    outcome, dm, rt, world = run(env, {"propose": [None]}, {"challenge": [{"objections": []}, {"objections": []}]}, aggressive=False)
    v = world.decision_report()["D1"]["versions"][0]
    assert v["kind"] == "NO_ACTION" and v["source"] == "fallback" and v["cost"]["worst"] == 8000


# ---- which lines of attack apply (computed in Python) -----------------------------------------
SMALL = {"worst_gbp": 200.0, "likely_gbp": 100.0, "best_gbp": 50.0, "baseline_quarterly_spend_gbp": 100000.0, "confidence": "High"}
CALM = {**EV, "impact": SMALL, "confidence": "High", "retries": 0, "sources": [EV["sources"][0]],
        "concentration": {**EV["concentration"], "country_share_pct": 5.0, "supplier_share_pct": 5.0}}


def test_checklist_lists_only_the_checks_that_apply():
    from core.red_team import checklist
    rows = decisions.compare_options(100000.0, IMPACT, ("HEDGE", 100))
    text = checklist(EV, rows, make_decision(100000.0, IMPACT, "HEDGE", 100, "r", 1, "model"))
    for n in (1, 2, 3, 4):
        assert f"{n}. " in text
    assert "5. " not in text  # nothing on the menu is cheaper or safer than a full hedge here


def test_calm_decision_has_no_applicable_check():
    from core.red_team import checklist
    rows = decisions.compare_options(100000.0, SMALL, ("NO_ACTION", 0))
    assert checklist(CALM, rows, make_decision(100000.0, SMALL, "NO_ACTION", 0, "r", 1, "model")) == ""


def test_no_applicable_check_means_no_model_call_and_a_recommended_outcome(env):
    trace, world = env
    rt = FakeRT(trace, {})  # an empty script: any model call would raise KeyError
    dm = FakeDM(trace, {"propose": [{"kind": "NO_ACTION", "share_pct": 0, "rationale": "Small move, monitor."}]})
    outcome = challenge_decision(CALM, dm, rt, world, trace)
    assert rt.calls == [] and rt.last_clear
    assert outcome["status"] == "RECOMMENDED" and outcome["objection_count"] == 0 and outcome["rounds"] == 0
