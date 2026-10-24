"""Meterless World Model - Python implementation of the engine spec (engines/world-model/AGENTS.md).

Event-sourced, deterministic, single-writer: every write goes through one lock per instance, so
concurrent agents (Vidhur runs its analysts in a thread pool) serialize and never interleave a write.
The five write primitives are upsert_entity, upsert_context, relate, assert_fact and ingest; everything
else is a derived view. Every write carries provenance: no provenance, no write.
"""

import copy
import threading
import time
from datetime import datetime, timezone

from .ingest import run_ingest_pipeline, world_state_shape
from .resolve import decide, name_similarity
from .stable_ids import (canonical_json, context_id_from_attrs, entity_id_from_name, external_id, fact_id,
                         relationship_id)
from .store import EventStore, iso_now, to_iso


class ProvenanceError(ValueError):
    """Raised for a write that carries no provenance."""


class WorldModel:
    def __init__(self, namespace, path=None, clock=None, auto_merge_at=0.85, review_lower=0.82,
                 conflict_policy="most-recent-wins", source_priority=None):
        if not namespace:
            raise ValueError("WorldModel: namespace is required")
        self.namespace = namespace
        self.store = EventStore(namespace, path, conflict_policy, source_priority)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._auto_merge_at, self._review_lower = auto_merge_at, review_lower
        self._conflict_policy = conflict_policy

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _provenance(source):
        if not source or not source.get("kind") or source.get("at") is None:
            raise ProvenanceError("World Model: every write carries provenance (kind + at). No provenance, no write.")
        return {**source, "at": to_iso(source["at"])}

    def _entity_copy(self, id_):
        e = self.store.state["entities"].get(id_)
        return copy.deepcopy(e) if e else None

    # ---------------------------------------------------------------- writes
    def upsert_entity(self, type, external_key=None, name=None, attrs=None, source=None, state=None):
        with self._lock:
            src = self._provenance(source)
            name = name or (attrs or {}).get("name")
            if external_key:
                id_ = external_id(type, external_key["system"], external_key["id"])
            else:
                if not name:
                    raise ValueError("World Model: name-keyed entities need a name (or provide an external_key)")
                id_ = entity_id_from_name(type, name)

            # Resolution pass for name-keyed entities: near-duplicates auto-merge or queue for review.
            if not external_key and name:
                for other in list(self.store.state["entities"].values()):
                    if other["type"] != type or other["id"] == id_ or not other.get("name"):
                        continue
                    similarity = name_similarity(name, other["name"])
                    decision = decide(similarity, self._auto_merge_at, self._review_lower)
                    if decision == "auto-merge":
                        self.store.append("alias", {"aliasId": id_, "primaryId": other["id"], "mergedAt": src["at"],
                                                    "mergedBy": "resolver", "reason": f"similarity {similarity:.3f}"}, src["at"])
                        self.store.log_audit("auto-merge", "resolver", f"{name} -> {other['name']} ({similarity:.3f})", at=src["at"])
                        return self._fold_entity_upsert(other["id"], type, attrs, src, name, state)
                    if decision == "review":
                        if not any({p["leftId"], p["rightId"]} == {id_, other["id"]} for p in self.store.merge_queue):
                            self.store.merge_queue.append({
                                "id": f"merge_{len(self.store.merge_queue) + 1}", "leftId": id_, "rightId": other["id"],
                                "leftName": name, "rightName": other["name"], "similarity": similarity,
                                "reason": "name similarity in review band [0.82, 0.85)", "status": "pending",
                                "proposedAt": src["at"]})
                            self.store.log_audit("merge-proposed", "resolver",
                                                 f"{name} ~ {other['name']} ({similarity:.3f}) queued", at=src["at"])
            return self._fold_entity_upsert(id_, type, attrs, src, name, state)

    def _fold_entity_upsert(self, id_, type, attrs, src, name, state=None):
        resolved = self.store.resolve_alias(id_)
        entity = {"type": type, "id": resolved, "attrs": dict(attrs or {}), "schemaVersion": 1, "source": src,
                  "createdAt": src["at"], "updatedAt": src["at"], "name": name,
                  "aliases": [name] if name else [], "state": state or "active"}
        event = self.store.append("upsertEntity", entity, src["at"])
        self.store.log_audit("upsertEntity", src.get("by") or "system", f"{resolved} ({type})",
                             event["id"], src.get("runId"), src["at"])
        return self._entity_copy(resolved)

    def upsert_context(self, type, external_key=None, attrs=None, parent=None, source=None):
        with self._lock:
            src = self._provenance(source)
            id_ = (external_id(type, external_key["system"], external_key["id"]) if external_key
                   else context_id_from_attrs(type, attrs or {}, parent))
            context = {"type": type, "id": id_, "attrs": dict(attrs or {}), "parent": parent, "schemaVersion": 1,
                       "source": src, "createdAt": src["at"], "updatedAt": src["at"]}
            event = self.store.append("upsertContext", context, src["at"])
            self.store.log_audit("upsertContext", src.get("by") or "system", f"{id_} ({type})",
                                 event["id"], src.get("runId"), src["at"])
            return copy.deepcopy(self.store.state["contexts"][id_])

    def relate(self, from_, type, to, context=None, attrs=None, valid_from=None, valid_to=None, source=None):
        with self._lock:
            src = self._provenance(source)
            f, t = self.store.resolve_alias(from_), self.store.resolve_alias(to)
            id_ = relationship_id(f, type, t, context, valid_from)
            if id_ in self.store.state["relationships"]:
                return copy.deepcopy(self.store.state["relationships"][id_])  # content-addressed no-op
            rel = {"id": id_, "from": f, "type": type, "to": t, "context": context, "attrs": attrs,
                   "validFrom": valid_from or src["at"], "validTo": valid_to, "schemaVersion": 1, "source": src,
                   "createdAt": src["at"]}
            event = self.store.append("relate", rel, src["at"])
            self.store.log_audit("relate", src.get("by") or "system", f"{f} -[{type}]-> {t}",
                                 event["id"], src.get("runId"), src["at"])
            return copy.deepcopy(self.store.state["relationships"][id_])

    def assert_fact(self, about, predicate, value, confidence=None, context=None, source=None):
        with self._lock:
            src = self._provenance(source)
            about = self.store.resolve_alias(about)
            id_ = fact_id(about, predicate, value, src["at"], src["kind"], src.get("url"))
            if id_ in self.store.state["facts"]:
                return copy.deepcopy(self.store.state["facts"][id_])
            fact = {"id": id_, "about": about, "predicate": predicate, "value": value,
                    "confidence": 0.5 if confidence is None else confidence, "context": context, "source": src,
                    "assertedAt": src["at"]}

            if about not in self.store.state["entities"]:  # make sure the subject exists first
                inferred = about.split(":")[0] if ":" in about else "entity"
                self.store.append("upsertEntity", {"type": inferred, "id": about, "attrs": {}, "schemaVersion": 1,
                                                   "source": src, "createdAt": src["at"], "updatedAt": src["at"],
                                                   "aliases": [], "state": "emerging"}, src["at"])
            # The store's fold applies the conflict policy and lands the winning value on the entity.
            event = self.store.append("assertFact", fact, src["at"])
            winner = self.store.state["facts"][id_]
            winner = self.store.state["facts"].get(winner.get("supersededBy"), winner)
            self.store.log_audit("assertFact", src.get("by") or "system",
                                 f"{about}.{predicate} = {canonical_json(winner['value'])} ({self._conflict_policy})",
                                 event["id"], src.get("runId"), src["at"])
            return copy.deepcopy(self.store.state["facts"][id_])

    # ---------------------------------------------------------------- ingest
    def ingest(self, observations):
        with self._lock, self.store.batch():
            def upsert(obs):
                src = self._provenance({**obs["source"], "at": obs["at"]})
                type_ = obs.get("type") or "item"
                entity = self._fold_entity_upsert(external_id(type_, "ingest", obs["externalId"]), type_,
                                                  {"title": obs["title"], "text": obs.get("text")}, src, obs["title"])
                entity_ids = []
                for mention in obs.get("entities") or []:
                    mid = entity_id_from_name(mention["type"], mention["name"])
                    self._fold_entity_upsert(mid, mention["type"], {}, src, mention["name"])
                    resolved = self.store.resolve_alias(mid)
                    entity_ids.append(resolved)
                    rid = relationship_id(entity["id"], "mentions", resolved)
                    if rid not in self.store.state["relationships"]:
                        self.store.append("relate", {"id": rid, "from": entity["id"], "type": "mentions", "to": resolved,
                                                     "validFrom": src["at"], "schemaVersion": 1, "source": src,
                                                     "createdAt": src["at"]}, src["at"])
                return entity["id"], sorted(entity_ids)

            return run_ingest_pipeline(self.store, observations, upsert, lambda: time.monotonic() * 1000)

    # ---------------------------------------------------------------- reads
    def view(self, name):
        views = {"graph": GraphView, "facts": FactsView, "snapshot": SnapshotView, "timeline": TimelineView}
        if name not in views:
            raise ValueError(f'World Model: unknown view "{name}"')
        return views[name](self.store, self._lock)

    # -------------------------------------------------------------- operator
    def merge_queue(self):
        return copy.deepcopy(self.store.merge_queue)

    def approve_merge(self, proposal_id, by):
        with self._lock:
            p = next((m for m in self.store.merge_queue if m["id"] == proposal_id and m["status"] == "pending"), None)
            if not p:
                raise ValueError(f"World Model: no pending merge proposal {proposal_id}")
            at = to_iso(self._clock())
            p.update(status="approved", resolvedAt=at, resolvedBy=by)
            self.store.append("alias", {"aliasId": p["leftId"], "primaryId": p["rightId"], "mergedAt": at,
                                        "mergedBy": by, "reason": p["reason"]}, at)
            self.store.log_audit("merge-approved", by, f"{p['leftName']} -> {p['rightName']}", at=at)

    def reject_merge(self, proposal_id, by):
        with self._lock:
            p = next((m for m in self.store.merge_queue if m["id"] == proposal_id and m["status"] == "pending"), None)
            if not p:
                raise ValueError(f"World Model: no pending merge proposal {proposal_id}")
            p.update(status="rejected", resolvedAt=to_iso(self._clock()), resolvedBy=by)
            self.store.log_audit("merge-rejected", by, f"{p['leftName']} stays separate from {p['rightName']}")

    def audit(self):
        with self._lock:
            return copy.deepcopy(self.store.audit)

    def events(self):
        with self._lock:
            return copy.deepcopy(self.store.events)

    def state_shape(self):
        with self._lock:
            return copy.deepcopy(world_state_shape(self.store))

    def state_shape_canonical(self):
        with self._lock:
            return canonical_json(world_state_shape(self.store))

    def subscribe_events(self, fn):
        return self.store.subscribe(fn)

    def persist(self):
        with self._lock:
            self.store.persist()


# ------------------------------------------------------------------ views
def _valid_at(rel, at):
    if rel.get("validFrom") and at < rel["validFrom"]:
        return False
    if rel.get("validTo") and at >= rel["validTo"]:
        return False
    return True


class _View:
    def __init__(self, store, lock):
        self.store, self._lock = store, lock


class GraphView(_View):
    def entity(self, id_):
        with self._lock:
            e = self.store.state["entities"].get(self.store.resolve_alias(id_))
            return copy.deepcopy(e) if e else None

    def neighbors_of(self, id_, via=None, in_context=None, at=None):
        with self._lock:
            rid, out = self.store.resolve_alias(id_), []
            for rel in self.store.state["relationships"].values():
                if via and rel["type"] != via:
                    continue
                if in_context and rel.get("context") != in_context:
                    continue
                if at and not _valid_at(rel, at):
                    continue
                if not at and rel.get("validTo"):
                    continue  # closed edges are excluded from "now" queries
                other = rel["to"] if rel["from"] == rid else rel["from"] if rel["to"] == rid else None
                entity = self.store.state["entities"].get(other) if other else None
                if entity:
                    out.append(copy.deepcopy(entity))
            return sorted(out, key=lambda e: e["id"])

    def outgoing(self, id_, via=None):
        """Entities this one points at, along open edges (e.g. claim -[audited-by]-> verdict)."""
        with self._lock:
            rid = self.store.resolve_alias(id_)
            out = [self.store.state["entities"].get(r["to"]) for r in self.store.state["relationships"].values()
                   if r["from"] == rid and (not via or r["type"] == via) and not r.get("validTo")]
            return sorted((copy.deepcopy(e) for e in out if e), key=lambda e: e["id"])

    def incoming(self, id_, via=None):
        with self._lock:
            rid = self.store.resolve_alias(id_)
            out = [self.store.state["entities"].get(r["from"]) for r in self.store.state["relationships"].values()
                   if r["to"] == rid and (not via or r["type"] == via) and not r.get("validTo")]
            return sorted((copy.deepcopy(e) for e in out if e), key=lambda e: e["id"])

    def query(self, type=None, where=None):
        with self._lock:
            return sorted((copy.deepcopy(e) for e in self.store.state["entities"].values()
                           if (not type or e["type"] == type) and (not where or where(e))), key=lambda e: e["id"])


class FactsView(_View):
    def about(self, id_):
        with self._lock:
            rid = self.store.resolve_alias(id_)
            return sorted((copy.deepcopy(f) for f in self.store.state["facts"].values() if f["about"] == rid),
                          key=lambda f: f["assertedAt"])


class SnapshotView(_View):
    def entity_at(self, id_, at):
        """Entity state as of a timestamp: replay the events with event.at <= at."""
        with self._lock:
            iso = f"{at}T23:59:59.999Z" if len(at) == 10 else at
            state = self.store.fold_until(iso)
            cur, seen = id_, set()
            while cur in state["aliases"] and cur not in seen:
                seen.add(cur)
                cur = state["aliases"][cur]
            e = state["entities"].get(cur)
            return copy.deepcopy(e) if e else None


class TimelineView(_View):
    def get(self):
        with self._lock:
            return copy.deepcopy(self.store.events)

    def subscribe(self, fn):
        return self.store.subscribe(fn)
