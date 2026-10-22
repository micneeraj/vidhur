"""The canonical batch/stream ingest cycle (spec: AGENTS.md section 4.1, docs/ingest-pipeline.md).

Idempotency contract: re-running the same batch produces the same world state and an empty diff.
Every derived timestamp comes from the OBSERVATION, never the wall clock.
"""

import math
from datetime import datetime, timezone

from .resolve import jaccard
from .stable_ids import canonical_json, sha256_hex

CLUSTER_JACCARD = 0.5
HOUR_MS = 3600 * 1000


def _ms(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _days_before(iso, days):
    return _iso(_ms(iso) - days * 24 * 3600 * 1000)


def world_state_shape(store):
    """The idempotency diff surface: everything derived from observations, in canonical order."""
    def by(d, key):
        return sorted(d.values(), key=lambda x: x[key])
    return {
        "entities": by(store.state["entities"], "id"),
        "contexts": by(store.state["contexts"], "id"),
        "relationships": by(store.state["relationships"], "id"),
        "facts": by(store.state["facts"], "id"),
        "observations": by(store.observations, "externalId"),
        "stories": by(store.stories, "id"),
    }


def run_ingest_pipeline(store, observations, upsert, timer):
    run_id = "run_" + sha256_hex(canonical_json([o["externalId"] for o in observations]))[:12]
    timings = []
    before = canonical_json(world_state_shape(store))
    events_before = len(store.events)

    def timed(step, name, fn):
        t0 = timer()
        out = fn()
        timings.append({"step": step, "name": name, "ms": timer() - t0})
        return out

    # 1. Reconcile: recompute staleness from observation times.
    def reconcile():
        latest = max((o["at"] for o in observations), default="")
        for rec in store.observations.values():
            rec["stale"] = latest != "" and rec["at"] < _days_before(latest, 30)
    timed(1, "reconcile-stale", reconcile)

    # 2. Classify: unchanged observations (same checksum) are skipped.
    fresh, skipped = [], []

    def classify():
        for obs in observations:
            checksum = sha256_hex(canonical_json({"externalId": obs["externalId"], "title": obs["title"],
                                                  "text": obs.get("text") or "", "at": obs["at"]}))
            existing = store.observations.get(obs["externalId"])
            (skipped if existing and existing["checksum"] == checksum else fresh).append((obs, checksum))
    timed(2, "cluster-classify", classify)

    # 3. Persist primary aggregates: observation entities + the entities they mention.
    def persist_aggregates():
        for obs, checksum in fresh:
            entity_id, entity_ids = upsert(obs)
            store.observations[obs["externalId"]] = {
                "id": "obs_" + sha256_hex(obs["externalId"])[:12], "externalId": obs["externalId"],
                "entityId": entity_id, "checksum": checksum, "at": obs["at"], "entityIds": entity_ids, "stale": False}
    timed(3, "persist-aggregates", persist_aggregates)

    # Group observations into stories by entity overlap (Jaccard >= 0.5).
    clusters = []

    def group():
        allobs = sorted(store.observations.values(), key=lambda o: o["externalId"])
        used = set()
        for seed in allobs:
            if seed["externalId"] in used:
                continue
            members = [seed]
            used.add(seed["externalId"])
            for other in allobs:
                if other["externalId"] in used:
                    continue
                if jaccard(seed.get("entityIds") or [], other.get("entityIds") or []) >= CLUSTER_JACCARD:
                    members.append(other)
                    used.add(other["externalId"])
            clusters.append(members)
    timed(2, "story-grouping", group)

    # 4-5, 8. Score (velocity over bucketed windows), infer milestones, patch source items.
    def score():
        for members in clusters:
            member_ids = sorted(m["externalId"] for m in members)
            story_id = "story_" + sha256_hex(canonical_json(member_ids))[:12]
            times = sorted(_ms(m["at"]) for m in members)
            last = times[-1]

            def velocity(window_ms):
                return len([t for t in times if t > last - window_ms]) / (window_ms / HOUR_MS)
            prev_hour = len([t for t in times if last - 2 * HOUR_MS < t <= last - HOUR_MS])
            curr_hour = len([t for t in times if t > last - HOUR_MS])
            snapshot = {"storyId": story_id, "at": _iso(last), "velocity1h": velocity(HOUR_MS),
                        "velocity24h": velocity(24 * HOUR_MS), "velocity7d": velocity(7 * 24 * HOUR_MS),
                        "trendDirection": int(math.copysign(1, curr_hour - prev_hour)) if curr_hour != prev_hour else 0}
            store.scores = [s for s in store.scores if s["storyId"] != story_id] + [snapshot]
            entity_ids = sorted({e for m in members for e in (m.get("entityIds") or [])})
            store.stories[story_id] = {
                "id": story_id, "title": members[0]["externalId"], "memberIds": member_ids,
                "entityNames": list(entity_ids), "entityIds": entity_ids, "sources": [],
                "firstAt": _iso(times[0]), "lastAt": _iso(last),
                "milestones": [{"kind": "multi-source", "at": _iso(times[1])}] if len(members) >= 2 else []}
            for m in members:
                m["storyId"] = story_id
    timed(4, "score-measure", score)

    # 9. Rebuild aggregates (entity mention counts, first/last seen, linked stories).
    def rebuild():
        for entity in store.state["entities"].values():
            mentions = [o for o in store.observations.values() if entity["id"] in (o.get("entityIds") or [])]
            if not mentions:
                continue
            times = sorted(m["at"] for m in mentions)
            entity["aggregates"] = {"mentionCount": len(mentions), "firstSeenAt": times[0], "lastSeenAt": times[-1],
                                    "linkedStoryIds": sorted({m["storyId"] for m in mentions if m.get("storyId")})}
    timed(9, "rebuild-aggregates", rebuild)

    store.persist()
    after = canonical_json(world_state_shape(store))
    report = {"runId": run_id, "processed": len(fresh), "skippedUnchanged": len(skipped), "clusters": len(clusters),
              "eventsAppended": len(store.events) - events_before, "changed": before != after, "stepTimingsMs": timings}
    store.log_audit("ingest", "pipeline", f"run {run_id}: {len(fresh)} processed, {len(skipped)} skipped",
                    None, run_id, observations[0]["at"] if observations else None)
    return report
