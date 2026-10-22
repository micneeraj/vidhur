"""Vidhur's shared memory for one run, built on the Meterless World Model engine (engines/world_model).

Every agent writes what it decides here and reads what the others decided from here:
  Planner  -> plan + one `delegation` entity per (analyst, part) decision, and its review decision
  Analyst  -> `claim` entities (one per attempt, chained with `revised-into`), the fetched `article` observations
  Evaluator-> `verdict` entities linked to the claim they judged (`audited-by`); it READS the claim from here
  Impact   -> `impact` entities linked to the claim (`has-impact`)
The CFO brief is built from `report()`, which only reads the world state.
The whole event log is persisted to logs/world_state_<run_id>.json.
"""

from datetime import datetime, timezone

from core.claims import Claim
from core.config import ROOT
from engines.world_model import WorldModel, external_id

SYSTEM = "vidhur"


def _now():
    return datetime.now(timezone.utc)


class RunWorld:
    def __init__(self, run_id, path=None):
        self.run_id = run_id
        self.path = path or ROOT / "logs" / f"world_state_{run_id}.json"
        self.world = WorldModel(f"vidhur-{run_id}", path=str(self.path))
        self.graph = self.world.view("graph")

    # ------------------------------------------------------------------ helpers
    def _src(self, by, url=None):
        return {"kind": "agent", "by": by, "at": _now(), "runId": self.run_id, **({"url": url} if url else {})}

    def key(self, type_, local_id):
        return external_id(type_, SYSTEM, f"{self.run_id}-{local_id}")

    def _upsert(self, type_, local_id, attrs, by, name=None):
        self.world.upsert_entity(type_, external_key={"system": SYSTEM, "id": f"{self.run_id}-{local_id}"},
                                 name=name, attrs=attrs, source=self._src(by))
        return self.key(type_, local_id)

    def _link(self, from_key, rel, to_key, by, multi=False):
        """multi=True for one-to-many links: the engine treats same-type edges from one entity as single-valued
        (a new one closes the old one) unless they carry different contexts."""
        return self.world.relate(from_key, rel, to_key, context=to_key if multi else None, source=self._src(by))

    # ------------------------------------------------------------------ run + plan
    def start_run(self, meta):
        return self._upsert("run", "run", {"status": "running", **meta}, "main")

    def finish_run(self, summary):
        return self._upsert("run", "run", {"status": "finished", **summary}, "main")

    def record_plan(self, plan, targets):
        """plan = {"delegations": [...], "skipped": [...], "summary": str, "fallback": bool}"""
        plan_key = self._upsert("plan", "plan", {
            "summary": plan.get("summary", ""), "fallback": bool(plan.get("fallback")),
            "targets": [t for t in targets]}, "planner")
        self._link(self.key("run", "run"), "has-plan", plan_key, "planner")
        for d in plan["delegations"]:
            key = self._upsert("delegation", d["work_id"], {**d, "action": "run", "status": "planned"}, "planner")
            self._link(plan_key, "delegates", key, "planner", multi=True)
        for i, s in enumerate(plan.get("skipped", []), 1):
            key = self._upsert("delegation", f"S{i}", {**s, "work_id": f"S{i}", "action": "skip", "status": "skipped"}, "planner")
            self._link(plan_key, "skips", key, "planner", multi=True)
        return plan_key

    def add_delegation(self, delegation, by="planner"):
        key = self._upsert("delegation", delegation["work_id"], {**delegation, "action": "run", "status": "planned"}, by)
        self._link(self.key("plan", "plan"), "delegates", key, by, multi=True)
        return key

    def record_review(self, review):
        key = self._upsert("review", "review", review, "planner")
        self._link(self.key("plan", "plan"), "reviewed-as", key, "planner")
        return key

    def set_delegation_status(self, work_id, status, by, **attrs):
        self._upsert("delegation", work_id, {"status": status, **attrs}, by)

    # ------------------------------------------------------------------ evidence
    def record_articles(self, articles, part_name, country, by):
        """Mirror fetched news into the world as observations (idempotent: re-ingest is a no-op)."""
        obs = [{"externalId": a.url, "type": "article", "title": a.title, "text": a.summary or None, "at": a.published,
                "source": {"kind": "rss" if a.feed != "frankfurter-api" else "api", "by": by, "url": a.url,
                           "runId": self.run_id},
                "entities": [{"type": "country", "name": country}, {"type": "part", "name": part_name},
                             {"type": "publisher", "name": a.publisher}]} for a in articles]
        return self.world.ingest(obs) if obs else None

    # ------------------------------------------------------------------ claims
    def write_claim(self, claim, work_id, by):
        local = f"{claim.claim_id}-a{claim.attempt}"
        key = self._upsert("claim", local, {**claim.to_dict(), "work_id": work_id}, by)
        self._link(self.key("delegation", work_id), "produced", key, by, multi=True)
        if claim.attempt > 1:
            self._link(self.key("claim", f"{claim.claim_id}-a{claim.attempt - 1}"), "revised-into", key, by)
        self.world.assert_fact(self.key("delegation", work_id), "latest_claim", key, source=self._src(by))
        return key

    def get_claim(self, claim_key):
        entity = self.graph.entity(claim_key)
        if entity is None:
            raise KeyError(f"claim {claim_key} is not in the world state")
        return Claim.from_dict(entity["attrs"])

    def latest_claim_key(self, work_id):
        """The most recent claim of a work item, from the world's fact log (newest assertion wins)."""
        facts = [f for f in self.world.view("facts").about(self.key("delegation", work_id)) if f["predicate"] == "latest_claim"]
        return facts[-1]["value"] if facts else None

    # ------------------------------------------------------------------ verdicts
    def write_verdict(self, claim_key, verdict, retry_count, by="evaluator"):
        claim = self.get_claim(claim_key)
        local = f"{claim.claim_id}-a{claim.attempt}"
        key = self._upsert("verdict", local, {"verdict": verdict.verdict, "reasons": verdict.reasons,
                                               "independent_sources": verdict.independent_sources,
                                               "strength": verdict.strength, "llm_checked": verdict.llm_checked,
                                               "retry_count": retry_count, "claim": claim_key}, by)
        self._link(claim_key, "audited-by", key, by)
        return key

    def verdicts_for(self, claim_key):
        return [v["attrs"] for v in self.graph.outgoing(claim_key, via="audited-by")]

    def write_final(self, work_id, status, retries, by="evaluator"):
        self.set_delegation_status(work_id, status, by, retries=retries)

    # ------------------------------------------------------------------ impact
    def write_impact(self, work_id, claim_key, impact, confidence, quarter, by="impact"):
        claim = self.get_claim(claim_key)
        key = self._upsert("impact", f"{claim.claim_id}", {**impact.__dict__, "confidence": confidence,
                                                          "quarter": quarter, "claim": claim_key,
                                                          "work_id": work_id}, by)
        self._link(claim_key, "has-impact", key, by)
        return key

    def write_error(self, work_id, message, by="main"):
        self.set_delegation_status(work_id, "NOT_ASSESSED", by, error=message)

    def article(self, url):
        """A fetched article as stored in the world (title, publisher, published), or None."""
        e = self.graph.entity(external_id("article", "ingest", url))
        if e is None:
            return None
        publishers = [p for p in self.graph.outgoing(e["id"], via="mentions") if p["type"] == "publisher"]
        return {"title": e["attrs"].get("title") or e.get("name", ""), "published": e["source"]["at"],
                "publisher": publishers[0]["name"] if publishers else "?"}

    # ------------------------------------------------------------------ report
    def report(self):
        """Everything the CFO brief needs, read back from the world state only."""
        g = self.graph
        run = g.entity(self.key("run", "run"))
        plan = g.entity(self.key("plan", "plan"))
        review = g.entity(self.key("review", "review"))
        delegations = sorted((d["attrs"] for d in g.query(type="delegation")), key=lambda a: a["work_id"])
        items = []
        for d in delegations:
            item = {"delegation": d, "attempts": [], "impact": None}
            claim_keys = sorted(c["id"] for c in g.outgoing(self.key("delegation", d["work_id"]), via="produced"))
            for ck in claim_keys:
                claim = self.get_claim(ck)
                verdicts = self.verdicts_for(ck)
                item["attempts"].append({"key": ck, "claim": claim, "verdicts": verdicts})
                impacts = g.outgoing(ck, via="has-impact")
                if impacts:
                    item["impact"] = impacts[0]["attrs"]
            items.append(item)
        return {"run": run["attrs"] if run else {}, "plan": plan["attrs"] if plan else {},
                "review": review["attrs"] if review else None, "items": items,
                "event_count": len(self.world.events()), "path": str(self.path)}
