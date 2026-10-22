"""Vidhur on the World Model: writes are visible to other agents, and a run's final state holds everything.

The engine's own conformance/golden tests are in test_world_model_engine.py. These use no model calls: they test
the shared memory itself (core/shared_state.py), with the Evaluator running its real deterministic checks.
"""

import threading
from datetime import datetime, timedelta, timezone

from agents.evaluator import Evaluator
from core.claims import Claim, Verdict
from core.impact import compute_impact
from core.news import Article
from core.shared_state import RunWorld
from core.trace import Trace
from engines.world_model import WorldModel

NOW = datetime.now(timezone.utc)
FRESH = (NOW - timedelta(days=2)).isoformat(timespec="seconds")
CATALOG = {"P001": {"part_name": "GangaAccel GA100 GPU Chip", "country": "Taiwan"}}


def make_world(tmp_path, run_id="t1"):
    return RunWorld(run_id, path=tmp_path / f"world_state_{run_id}.json")


def claim(attempt=1, urls=("https://a.test/1", "https://b.test/2")):
    return Claim("D1", "Export curbs raise GPU costs.", "P001", "GangaAccel GA100 GPU Chip", "Taiwan", "cost_up", 3.0, 8.0,
                 list(urls), "because", attempt)


def plan():
    return {"summary": "Focus on the GPU chip.", "fallback": False,
            "delegations": [{"analyst": "geopolitics", "part_id": "P001", "reason": "largest spend", "work_id": "D1"}],
            "skipped": [{"analyst": "currency", "part_id": "P001", "reason": "no TWD data"}]}


def test_a_claim_written_by_one_agent_is_read_by_another(tmp_path):
    world = make_world(tmp_path)
    world.record_plan(plan(), [])
    key = world.write_claim(claim(), "D1", "geopolitics")
    # a different agent, holding only the key, reads the claim back from the world state
    read = world.get_claim(key)
    assert read.claim == "Export curbs raise GPU costs." and read.pct_high == 8.0 and read.source_urls == ["https://a.test/1", "https://b.test/2"]
    assert world.latest_claim_key("D1") == key


def test_evaluator_reads_the_claim_from_the_world_and_writes_its_verdict_back(tmp_path):
    world = make_world(tmp_path)
    world.record_plan(plan(), [])
    pool = {}  # nothing was fetched, so the cited urls are invented
    evaluator = Evaluator(CATALOG, pool, world, Trace(log_dir=tmp_path, quiet=True), model="m", now=NOW)
    key = world.write_claim(claim(), "D1", "geopolitics")
    verdict = evaluator.evaluate(key)  # only the key is passed - the claim itself comes from the world
    assert verdict.verdict == "SEND_BACK" and any("UNKNOWN_URL" in r for r in verdict.reasons)
    stored = world.verdicts_for(key)
    assert stored and stored[0]["verdict"] == "SEND_BACK" and stored[0]["claim"] == key


def test_revisions_are_chained_and_latest_wins(tmp_path):
    world = make_world(tmp_path)
    world.record_plan(plan(), [])
    first = world.write_claim(claim(1), "D1", "geopolitics")
    second = world.write_claim(claim(2, urls=("https://a.test/1", "https://b.test/2", "https://c.test/3")), "D1", "geopolitics")
    assert world.latest_claim_key("D1") == second != first
    assert [c["id"] for c in world.graph.outgoing(first, via="revised-into")] == [second]
    assert world.get_claim(first).attempt == 1 and world.get_claim(second).attempt == 2


def test_final_state_contains_plan_claims_verdicts_and_impacts(tmp_path):
    world = make_world(tmp_path)
    world.start_run({"run_id": "t1"})
    world.record_plan(plan(), [{"part_id": "P001"}])
    k1 = world.write_claim(claim(1, urls=("https://a.test/1",)), "D1", "geopolitics")
    world.write_verdict(k1, Verdict("SEND_BACK", ["TOO_FEW_SOURCES: only 1"], 1), 0)
    k2 = world.write_claim(claim(2), "D1", "geopolitics")
    world.write_verdict(k2, Verdict("PASS", ["supported"], 2, "strong", True), 1)
    world.write_final("D1", "PASS", 1)
    world.write_impact("D1", k2, compute_impact(1_000_000, 3, 8, "cost_up"), "Medium", "2026-Q3")
    world.record_review({"redelegate": False, "reason": "all verified"})
    world.finish_run({"elapsed_seconds": 5})

    report = world.report()
    assert report["plan"]["summary"] == "Focus on the GPU chip."
    delegations = {i["delegation"]["work_id"]: i["delegation"] for i in report["items"]}
    assert delegations["D1"]["status"] == "PASS" and delegations["S1"]["action"] == "skip"
    item = next(i for i in report["items"] if i["delegation"]["work_id"] == "D1")
    assert [a["claim"].attempt for a in item["attempts"]] == [1, 2]                      # claims, in order
    assert [v["verdict"] for a in item["attempts"] for v in a["verdicts"]] == ["SEND_BACK", "PASS"]  # verdicts
    assert item["impact"]["worst_gbp"] == 80_000.0 and item["impact"]["confidence"] == "Medium"        # impacts
    assert report["review"]["redelegate"] is False and report["run"]["status"] == "finished"


def test_world_state_is_persisted_and_replays_to_the_same_state(tmp_path):
    world = make_world(tmp_path, "persist")
    world.record_plan(plan(), [])
    key = world.write_claim(claim(), "D1", "geopolitics")
    world.write_verdict(key, Verdict("PASS", ["ok"], 2, "strong", True), 0)
    path = tmp_path / "world_state_persist.json"
    assert path.exists()
    replayed = WorldModel("vidhur-persist", path=str(path))
    assert replayed.state_shape_canonical() == world.world.state_shape_canonical()
    again = RunWorld("persist", path=path)  # a fresh process can pick the run up from the file
    assert again.get_claim(key).claim == "Export curbs raise GPU costs."


def test_articles_are_mirrored_into_the_world_idempotently(tmp_path):
    world = make_world(tmp_path)
    arts = [Article("Taiwan tightens chip exports", "Reuters", "https://r.test/1", FRESH),
            Article("Taipei curbs AI chip shipments", "Bloomberg", "https://b.test/1", FRESH)]
    world.record_articles(arts, "GangaAccel GA100 GPU Chip", "Taiwan", "geopolitics")
    shape = world.world.state_shape_canonical()
    assert world.record_articles(arts, "GangaAccel GA100 GPU Chip", "Taiwan", "geopolitics")["changed"] is False
    assert world.world.state_shape_canonical() == shape
    assert world.article("https://r.test/1")["publisher"] == "Reuters"


def test_concurrent_writes_from_many_agents_do_not_interleave(tmp_path):
    world = make_world(tmp_path)
    world.record_plan({"summary": "x", "fallback": False, "skipped": [],
                       "delegations": [{"analyst": "geopolitics", "part_id": "P001", "reason": "r", "work_id": f"D{i}"} for i in range(1, 7)]}, [])

    def work(i):
        c = Claim(f"D{i}", f"claim {i}", "P001", "x", "Taiwan", "cost_up", 1, 2, ["u1", "u2"], "r", 1)
        key = world.write_claim(c, f"D{i}", "geopolitics")
        world.write_verdict(key, Verdict("PASS", ["ok"], 2, "strong", True), 0)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(1, 7)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    seqs = [e["seq"] for e in world.world.events()]
    assert seqs == list(range(1, len(seqs) + 1))                      # one strictly ordered log
    assert len([a for a in world.world.audit()]) == len({a["seq"] for a in world.world.audit()})
    assert len(world.report()["items"]) == 6 and all(len(i["attempts"]) == 1 for i in world.report()["items"])
