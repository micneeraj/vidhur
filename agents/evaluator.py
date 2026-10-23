"""Evaluator: the auditor. Checks every claim before it can reach the CFO brief.

Deterministic checks run first (cheap, exact). Only claims that clear them go to the
Nemotron check, which asks whether the cited sources support the claim.
A failing claim is sent back to the analyst with the reason, at most twice, then marked UNVERIFIED.
The Evaluator READS the claim from the shared world state and WRITES its verdict back to it.
"""

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from core import config, llm
from core.claims import AuditResult, Verdict
from core.source_trust import publisher_key, trust_of

MAX_RETRIES = 2
SYNDICATION_TITLE_SIMILARITY = 0.88  # headlines this alike are treated as one story, not two sources
FX_TOLERANCE = 0.011  # percentage points; the analyst's range must equal the Python-computed moves

EVALUATOR_SYSTEM_PROMPT = (
    "You are the Evaluator (auditor) for a procurement cost-risk system. You judge whether the cited sources "
    "actually support an analyst's claim about the cost of a part bought by Ganga AI Solutions. "
    "Be strict about facts and fair about estimates: do not demand that the sources state a percentage; do reject "
    "the claim if the sources contradict it or if its direction is not supported. Judge only from the sources shown. "
    "Reply with a single JSON object and nothing else."
)

SCOPE = {
    "geopolitics": ("This is the GEOPOLITICS analyst: the driver must be a geopolitical or trade-policy development "
                    "(export controls, sanctions, tariffs, tensions, trade deals). If the claim rests on market demand, "
                    "energy or commodity prices or currency moves instead, answer supported=false and say it is out of scope."),
    "currency": ("This is the CURRENCY analyst. One source is the ECB exchange-rate data (its title carries the computed "
                 "moves) - treat those numbers as fact. The news sources need only be relevant to the currency or what "
                 "drives it. Answer supported=false if the explanation contradicts the rate data or the news."),
}


@dataclass
class Finding:
    code: str
    message: str


# ---------------------------------------------------------------------------
# Deterministic checks (pure functions - unit tested without any network)
# ---------------------------------------------------------------------------
def _norm_publisher(name):
    name = re.sub(r"^www\.", "", (name or "").lower().strip())
    name = re.sub(r"\.(com|co\.uk|org|net|in)$", "", name)
    return re.sub(r"[^a-z0-9]", "", name)


def _norm_title(title):
    return " ".join(re.findall(r"[a-z0-9]+", (title or "").lower()))


def independent_groups(articles):
    """Group articles that are the same publisher or the same syndicated story.

    Returns a list of groups; the number of groups is the number of independent sources.
    """
    parent = list(range(len(articles)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(articles)):
        for j in range(i + 1, len(articles)):
            same_publisher = (_norm_publisher(articles[i].publisher) == _norm_publisher(articles[j].publisher)
                              or publisher_key(articles[i].url, articles[i].publisher) == publisher_key(articles[j].url, articles[j].publisher))
            similar_title = (
                SequenceMatcher(None, _norm_title(articles[i].title), _norm_title(articles[j].title)).ratio()
                >= SYNDICATION_TITLE_SIMILARITY
            )
            if same_publisher or similar_title:
                parent[find(i)] = find(j)

    groups = {}
    for i, article in enumerate(articles):
        groups.setdefault(find(i), []).append(article)
    return list(groups.values())


def _currency_consistency(claim):
    """The currency analyst's direction and range must equal what Python computed from the FX data."""
    c = claim.computed or {}
    try:
        m30, m90 = float(c["move_30d_pct"]), float(c["move_90d_pct"])
    except (KeyError, TypeError, ValueError):
        return [Finding("FX_NO_DATA", "A currency claim must carry the computed FX moves; none were attached.")]
    low, high = sorted((round(abs(m30), 2), round(abs(m90), 2)))
    expected_dir = "cost_up" if m90 > 0 else "cost_down"
    out = []
    if claim.direction != expected_dir:
        out.append(Finding("FX_MISMATCH", f"The FX data (90d {m90:+.2f}%) implies {expected_dir}, not {claim.direction}."))
    if abs(claim.pct_low - low) > FX_TOLERANCE or abs(claim.pct_high - high) > FX_TOLERANCE:
        out.append(Finding("FX_MISMATCH", f"The range must be the computed FX moves {low}-{high}%, not {claim.pct_low}-{claim.pct_high}%."))
    return out


def source_profile(articles):
    """Evidence strength of a set of sources: independent count, tier counts, and a one-line summary."""
    groups = independent_groups(list(articles)) if articles else []
    tiers = [trust_of(a.url, a.publisher)[1] for a in articles]
    best_per_group = [min(trust_of(a.url, a.publisher)[1] for a in g) for g in groups]
    summary = f"{len(groups)} independent source{'s' if len(groups) != 1 else ''}"
    parts = [f"{best_per_group.count(t)} Tier {t}" for t in (1, 2, 3) if best_per_group.count(t)]
    return {"independent": len(groups), "tiers": tiers, "summary": summary + (", " + ", ".join(parts) if parts else "")}


def trust_findings(known, now, max_age_days):
    """NO_TRUSTED_SOURCE / SOURCES_NOT_INDEPENDENT / STALE_SOURCES for the sources a claim cites (all already fetched)."""
    cutoff = now - timedelta(days=max_age_days)
    fresh = [a for a in known if datetime.fromisoformat(a.published) >= cutoff]
    stale = [a for a in known if a not in fresh]
    out = []
    groups = independent_groups(fresh) if fresh else []
    if stale and len(groups) < 2:
        out.append(Finding("STALE_SOURCES", f"{len(stale)} cited source(s) are older than {max_age_days} days and do not count; only "
                           f"{len(groups)} independent fresh source(s) remain, at least 2 are required."))
    if known and not any(trust_of(a.url, a.publisher)[1] <= 2 for a in fresh):
        names = ", ".join(sorted({f"{a.publisher} (Tier {trust_of(a.url, a.publisher)[1]})" for a in known}))
        out.append(Finding("NO_TRUSTED_SOURCE", f"No fresh cited source is Tier 1 or Tier 2 (cited: {names}). "
                           "Cite at least one wire service, major business press or official source."))
    if len(fresh) >= 2 and len(groups) < 2:
        out.append(Finding("SOURCES_NOT_INDEPENDENT", f"{len(fresh)} fresh sources collapse to {len(groups)} independent one "
                           "(same publisher domain or near-identical headline count once)."))
    return out


def deterministic_checks(claim, pool, catalog, now=None, max_age_days=None):
    """Return a list of Findings (empty list = all deterministic checks passed).

    pool:    {url: Article} - everything actually fetched this run
    catalog: {part_id: {"part_name", "country"}} - Ganga's parts, read from Zetaris
    """
    now = now or datetime.now(timezone.utc)
    max_age_days = max_age_days or config.evidence_max_age_days()
    findings = []

    # 1. part and country exist in Ganga's own data
    part = catalog.get(claim.part_id)
    if part is None:
        findings.append(Finding("UNKNOWN_PART", f"Part {claim.part_id!r} does not exist in Ganga's purchase data."))
    elif part["country"].lower() != str(claim.supplier_country).lower():
        findings.append(Finding("COUNTRY_MISMATCH", f"Ganga buys {claim.part_id} from {part['country']}, not from {claim.supplier_country!r}."))
    if not any(p["country"].lower() == str(claim.supplier_country).lower() for p in catalog.values()):
        findings.append(Finding("UNKNOWN_COUNTRY", f"Ganga has no supplier in {claim.supplier_country!r}."))

    # 2. direction and estimate are well formed
    if claim.direction not in ("cost_up", "cost_down"):
        findings.append(Finding("BAD_DIRECTION", f"direction must be cost_up or cost_down, got {claim.direction!r}."))
    if any(math.isnan(v) for v in (claim.pct_low, claim.pct_high)) or not (0 <= claim.pct_low <= claim.pct_high <= 100):
        findings.append(Finding("BAD_RANGE", "The % change range must be numbers with 0 <= pct_low <= pct_high <= 100."))

    # 3. sources: count, existence, freshness, independence
    cited = list(dict.fromkeys(claim.source_urls))  # distinct urls, order kept
    if len(cited) < 2:
        findings.append(Finding("TOO_FEW_SOURCES", f"Only {len(cited)} source cited; at least 2 independent sources are required."))

    for url in (u for u in cited if u not in pool):
        findings.append(Finding("UNKNOWN_URL", f"Cited source {url[:80]!r} is not in the fetched news set; cite only fetched articles."))

    known = [pool[u] for u in cited if u in pool]
    cutoff = now - timedelta(days=max_age_days)
    for article in known:
        if datetime.fromisoformat(article.published) < cutoff:
            findings.append(Finding(
                "STALE_SOURCE",
                f"Source '{article.title[:70]}' ({article.publisher}) is from {article.published[:10]}, "
                f"older than {max_age_days} days; replace it with a fresher one."))

    if len(known) >= 2 and len(independent_groups(known)) < 2:
        names = sorted({a.publisher for a in known})
        findings.append(Finding(
            "NOT_INDEPENDENT",
            f"The cited sources are not independent (publishers: {', '.join(names)}; same publisher or the same "
            "story syndicated). Cite sources from different publishers reporting separately."))

    # 3b. trust layer: only fresh sources count; one of them must be Tier 1 or 2; copies of one report count once
    findings.extend(trust_findings(known, now, max_age_days))

    # 4. analyst-specific consistency with computed data
    if claim.analyst == "currency":
        findings.extend(_currency_consistency(claim))
    return findings


# ---------------------------------------------------------------------------
# The Evaluator
# ---------------------------------------------------------------------------
class Evaluator:
    name = "evaluator"

    def __init__(self, catalog, pool, world, trace, model=None, now=None, max_age_days=None):
        self.catalog = catalog
        self.pool = pool  # shared with the analysts: url -> Article
        self.world = world  # core.shared_state.RunWorld: claims are read from it, verdicts written to it
        self.trace = trace
        self.model = model or config.evaluator_model()
        self.expected_model = config.evaluator_expected() if model is None else model
        self.now = now
        self.max_age_days = max_age_days

    def evaluate(self, claim_key, retry_count=0):
        claim = self.world.get_claim(claim_key)  # read from the shared world state, not from a local variable
        findings = deterministic_checks(claim, self.pool, self.catalog, self.now, self.max_age_days)
        cited = [self.pool[u] for u in dict.fromkeys(claim.source_urls) if u in self.pool]
        independent = len(independent_groups(cited)) if cited else 0

        if findings:
            verdict = Verdict("SEND_BACK", [f"{f.code}: {f.message}" for f in findings], independent, llm_checked=False)
            note = "deterministic checks failed; model check skipped"
        else:
            supported, strength, reason = self._model_check(claim, cited, retry_count)
            verdict = (Verdict("PASS", [reason], independent, strength, llm_checked=True) if supported
                       else Verdict("SEND_BACK", [f"NOT_SUPPORTED: {reason}"], independent, strength, llm_checked=True))
            note = ""
        self.world.write_verdict(claim_key, verdict, retry_count)
        self.trace.event(
            self.name, "verdict", input_summary=f"{claim.claim_id} attempt {claim.attempt}: {claim.claim}",
            output_summary=" | ".join(verdict.reasons) + (f" ({note})" if note else ""),
            model=self.model if verdict.llm_checked else None, sources=claim.source_urls, decision=verdict.verdict,
            retry_count=retry_count, independent_sources=verdict.independent_sources)
        return verdict

    def _model_check(self, claim, cited, retry_count):
        articles = "\n".join(
            f"S{i} | {a.published[:10]} | {a.publisher} | {a.title}" + (f" - {a.summary[:300]}" if a.summary else "")
            for i, a in enumerate(cited, 1))
        prompt = (
            f"Claim: {claim.claim}\n"
            f"Part: {claim.part_id} {claim.part_name}, supplied from {claim.supplier_country}.\n"
            f"Analyst says unit cost goes {'UP' if claim.direction == 'cost_up' else 'DOWN'} by "
            f"{claim.pct_low}-{claim.pct_high}% (the analyst's estimate).\n"
            f"Analyst reasoning: {claim.reasoning}\n\nCited sources:\n{articles}\n\n"
            "Do these sources, read together, support the claim (the development happened, it plausibly affects this "
            "part from this country, and the direction is right)? " + SCOPE.get(claim.analyst, "") + " "
            "Keep your private reasoning brief. Reply exactly:\n"
            '{"supported": true or false, "strength": "strong" or "moderate" or "weak", "reason": "<one or two sentences>"}')
        result = llm.chat(
            [{"role": "system", "content": EVALUATOR_SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
            model=self.model, expected_model=self.expected_model, agent=self.name, step="support-check",
            trace=self.trace, retry_count=retry_count, max_tokens=4000)
        try:
            data = llm.extract_json(result.text)
            return bool(data.get("supported")), str(data.get("strength", "moderate")).lower(), str(data.get("reason", "")).strip()
        except ValueError:
            # An unreadable audit is never a pass.
            return False, "weak", "The Evaluator's model reply could not be parsed, so the claim is not accepted."


# ---------------------------------------------------------------------------
# The send-back loop
# ---------------------------------------------------------------------------
def audit_claim(claim_key, target, work_id, analyst, evaluator, world, trace, max_retries=MAX_RETRIES):
    """Evaluate a claim; on SEND_BACK return it to the analyst with the reason, up to max_retries times.

    Everything is read from and written to the world state, so the loop holds only the claim's key.
    """
    retries = 0
    while True:
        verdict = evaluator.evaluate(claim_key, retry_count=retries)
        if verdict.verdict == "PASS":
            world.write_final(work_id, "PASS", retries)
            return AuditResult(claim_key, "PASS", verdict, retries)

        if retries >= max_retries:
            trace.event(evaluator.name, "final", input_summary=work_id, output_summary=f"still failing after {retries} retries",
                        decision="UNVERIFIED", retry_count=retries)
            world.write_final(work_id, "UNVERIFIED", retries)
            return AuditResult(claim_key, "UNVERIFIED", verdict, retries)

        retries += 1
        trace.event(evaluator.name, "send-back", input_summary=work_id, output_summary=" | ".join(verdict.reasons),
                    decision="SEND_BACK", retry_count=retries)
        revised = analyst.revise(target, work_id)  # the analyst reads the feedback from the world state
        if revised is None:
            trace.event(analyst.name, "claim:withdrawn", input_summary=work_id,
                        output_summary="analyst found no adequate evidence after feedback", decision="UNVERIFIED", retry_count=retries)
            world.set_delegation_status(work_id, "UNVERIFIED", analyst.name, retries=retries, note="analyst withdrew the claim after feedback")
            return AuditResult(claim_key, "UNVERIFIED", verdict, retries)
        claim_key = world.latest_claim_key(work_id)
