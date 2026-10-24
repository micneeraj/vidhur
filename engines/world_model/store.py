"""Event-sourced canonical store: state is a fold over an append-only event log.

Views are projections of the fold. Optionally persisted as one JSON file that is replayed on load.
Port of the Meterless reference `store.ts`. Records use the reference's camelCase field names so the
persisted file and the canonical state shape are directly comparable with the TypeScript reference.
"""

import copy
import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone


def iso_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def to_iso(value):
    """Datetime or ISO string -> ISO string (UTC, millisecond precision for datetimes)."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return value


def empty_state():
    return {"entities": {}, "contexts": {}, "relationships": {}, "facts": {}, "aliases": {}}


class EventStore:
    def __init__(self, namespace, path=None, conflict_policy="most-recent-wins", source_priority=None):
        self.namespace = namespace
        self.conflict_policy = conflict_policy
        self.source_priority = source_priority or []
        self.events = []
        self.audit = []
        self.observations = {}
        self.stories = {}
        self.scores = []
        self.merge_queue = []
        self.state = empty_state()
        self._seq = 0
        self._audit_seq = 0
        self.path = path
        self._subscribers = []
        self._loading = False
        self._defer = 0  # >0 inside batch(): persist() only marks the store dirty
        self._dirty = False
        self._persist_lock = threading.RLock()
        if path and os.path.exists(path):
            self._load()

    # ------------------------------------------------------------------ writes
    def append(self, kind, payload, at):
        event = {"id": f"evt_{self._seq + 1}", "seq": self._seq + 1, "kind": kind, "payload": payload,
                 "schemaVersion": 1, "at": at}
        self._apply(event, is_new=True)
        return event

    def _apply(self, event, is_new):
        self._seq = max(self._seq, event["seq"])
        self.events.append(event)
        self._fold(event, self.state)
        if is_new:
            self.persist()
            for fn in list(self._subscribers):
                fn(event)

    def _priority(self, kind):
        return self.source_priority.index(kind) if kind in self.source_priority else 1 << 60

    def _fold(self, event, state):
        kind, p = event["kind"], event["payload"]
        if kind == "upsertEntity":
            existing = state["entities"].get(p["id"])
            if existing:
                existing["attrs"] = {**existing["attrs"], **p["attrs"]}
                existing["updatedAt"] = p["updatedAt"]
                existing["source"] = p["source"]
                if p.get("name") and p["name"] not in existing["aliases"]:
                    existing["aliases"].append(p["name"])
                if p.get("name"):
                    existing["name"] = p["name"]
            else:
                state["entities"][p["id"]] = copy.deepcopy(p)
        elif kind == "upsertContext":
            existing = state["contexts"].get(p["id"])
            if existing:
                existing["attrs"] = {**existing["attrs"], **p["attrs"]}
                existing["updatedAt"] = p["updatedAt"]
            else:
                state["contexts"][p["id"]] = copy.deepcopy(p)
        elif kind == "relate":
            # Superseding semantics: a new open edge with the same (from, type, context) but a
            # different target CLOSES the previous open edge. Nothing is ever deleted.
            for other in state["relationships"].values():
                if (other["from"] == p["from"] and other["type"] == p["type"]
                        and (other.get("context") or "") == (p.get("context") or "")
                        and other["to"] != p["to"] and not other.get("validTo") and not other.get("supersededBy")):
                    other["validTo"] = p.get("validFrom") or p["createdAt"]
                    other["supersededBy"] = p["id"]
            if p["id"] not in state["relationships"]:
                state["relationships"][p["id"]] = copy.deepcopy(p)
        elif kind == "assertFact":
            if p["id"] not in state["facts"]:
                self._land_fact(state, copy.deepcopy(p))
        elif kind == "alias":
            state["aliases"][p["aliasId"]] = p["primaryId"]
        # snapshot / tombstone / view-rebuild: recorded in the log only

    def _land_fact(self, state, fact):
        """Add a fact; the conflict policy picks which fact's value lands on the entity (supersededBy marks the rest).

        The reference implementation applies the winning value to the entity OUTSIDE the event log, so a replay
        would not reproduce it. Doing it inside the fold keeps state a pure function of the log.
        """
        rivals = [f for f in state["facts"].values() if f["about"] == fact["about"]
                  and f["predicate"] == fact["predicate"] and not f.get("supersededBy")]
        winner = fact
        for rival in rivals:
            if self.conflict_policy == "highest-confidence-wins":
                rival_wins = rival["confidence"] > winner["confidence"]
            elif self.conflict_policy == "source-priority":
                rival_wins = self._priority(rival["source"]["kind"]) < self._priority(winner["source"]["kind"])
            else:  # most-recent-wins
                rival_wins = rival["assertedAt"] > winner["assertedAt"]
            if rival_wins:
                winner = rival
        state["facts"][fact["id"]] = fact
        for f in [fact, *rivals]:
            if f["id"] != winner["id"]:
                state["facts"][f["id"]]["supersededBy"] = winner["id"]
        entity = state["entities"].get(fact["about"])
        if entity is not None:
            entity["attrs"][fact["predicate"]] = winner["value"]
            entity["updatedAt"] = fact["assertedAt"]

    def fold_until(self, at):
        """State as of a timestamp: replay the events with event.at <= at (the snapshot view)."""
        scratch = empty_state()
        for e in self.events:
            if e["at"] <= at:
                self._fold(e, scratch)
        return scratch

    def resolve_alias(self, id_):
        cur, seen = id_, set()
        while cur in self.state["aliases"] and cur not in seen:
            seen.add(cur)
            cur = self.state["aliases"][cur]
        return cur

    def log_audit(self, kind, actor, summary, event_id=None, run_id=None, at=None):
        self._audit_seq += 1
        self.audit.append({"seq": self._audit_seq, "at": at or "1970-01-01T00:00:00.000Z", "kind": kind,
                           "actor": actor, "summary": summary, "eventId": event_id, "runId": run_id})
        self.persist()

    def subscribe(self, fn):
        self._subscribers.append(fn)
        return lambda: self._subscribers.remove(fn)

    # ------------------------------------------------------------- persistence
    @contextmanager
    def batch(self):
        """Write the file once for everything appended inside the block (an ingest of 60 articles otherwise rewrites it 200+ times)."""
        with self._persist_lock:
            self._defer += 1
        try:
            yield
        finally:
            with self._persist_lock:
                self._defer -= 1
                if self._defer == 0 and self._dirty:
                    self._dirty = False
                    self.persist()

    def persist(self):
        if not self.path or self._loading:
            return
        with self._persist_lock:
            if self._defer:
                self._dirty = True
                return
            self._write()

    def _write(self):
        data = {"events": self.events, "audit": self.audit, "observations": list(self.observations.items()),
                "stories": list(self.stories.items()), "scores": self.scores, "mergeQueue": self.merge_queue}
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, ensure_ascii=False, default=str)
        os.replace(tmp, self.path)

    def _load(self):
        with open(self.path, encoding="utf-8") as f:
            saved = json.load(f)
        self._loading = True
        try:
            self.audit = saved.get("audit", [])
            self._audit_seq = self.audit[-1]["seq"] if self.audit else 0
            self.observations = dict(saved.get("observations", []))
            self.stories = dict(saved.get("stories", []))
            self.scores = saved.get("scores", [])
            self.merge_queue = saved.get("mergeQueue", [])
            for e in saved.get("events", []):
                self._apply(e, is_new=False)
        finally:
            self._loading = False
