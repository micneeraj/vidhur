"""Evaluator: the auditor. Checks every claim before it can reach the CFO brief.

Deterministic checks run first (cheap, exact). Only claims that clear them go to the
Nemotron check, which asks whether the cited sources really support the claim.
A failing claim is sent back to the analyst with the reason, at most twice, then marked UNVERIFIED.
"""

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from core import config, llm
from core.claims import AuditResult, Verdict

MAX_RETRIES = 2
SYNDICATION_TITLE_SIMILARITY = 0.88  # headlines this alike are treated as one story, not two sources

EVALUATOR_SYSTEM_PROMPT = (
    "You are the Evaluator (auditor) for a procurement cost-risk system. You judge whether the cited news "
    "articles actually support an analyst's claim about the cost of a part bought by Ganga AI Solutions. "
    "Be strict about facts and fair about estimates: the percentage range is the analyst's own estimate, so "
    "do not demand the articles state a percentage; do reject it if the articles contradict it or if the "
    "direction of the claim is not supported. Judge only from the articles shown. "
    "Reply with a single JSON object and nothing else."
)


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
            same_publisher = _norm_publisher(articles[i].publisher) == _norm_publisher(articles[j].publisher)
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
        findings.append(
            Finding(
                "COUNTRY_MISMATCH",
                f"Ganga buys {claim.part_id} from {part['country']}, not from {claim.supplier_country!r}.",
            )
        )
    if not any(p["country"].lower() == str(claim.supplier_country).lower() for p in catalog.values()):
        findings.append(Finding("UNKNOWN_COUNTRY", f"Ganga has no supplier in {claim.supplier_country!r}."))

    # 2. direction and estimate are well formed
    if claim.direction not in ("cost_up", "cost_down"):
        findings.append(Finding("BAD_DIRECTION", f"direction must be cost_up or cost_down, got {claim.direction!r}."))
    values = (claim.pct_low, claim.pct_high)
    if any(math.isnan(v) for v in values) or not (0 <= claim.pct_low <= claim.pct_high <= 100):
        findings.append(Finding("BAD_RANGE", "The % change range must be numbers with 0 <= pct_low <= pct_high <= 100."))

    # 3. sources: count, existence, freshness, independence
    cited = list(dict.fromkeys(claim.source_urls))  # distinct urls, order kept
    if len(cited) < 2:
        findings.append(
            Finding("TOO_FEW_SOURCES", f"Only {len(cited)} source cited; at least 2 independent sources are required.")
        )

    unknown = [u for u in cited if u not in pool]
    for url in unknown:
        findings.append(
            Finding("UNKNOWN_URL", f"Cited source {url[:80]!r} is not in the fetched news set; cite only fetched articles.")
        )

    known = [pool[u] for u in cited if u in pool]
    cutoff = now - timedelta(days=max_age_days)
    for article in known:
        if datetime.fromisoformat(article.published) < cutoff:
            findings.append(
                Finding(
                    "STALE_SOURCE",
                    f"Source '{article.title[:70]}' ({article.publisher}) is from {article.published[:10]}, "
                    f"older than {max_age_days} days; replace it with a fresher one.",
                )
            )

    if len(known) >= 2:
        groups = independent_groups(known)
        if len(groups) < 2:
            names = sorted({a.publisher for a in known})
            findings.append(
                Finding(
                    "NOT_INDEPENDENT",
                    f"The cited sources are not independent (publishers: {', '.join(names)}; same publisher or the same "
                    "story syndicated). Cite sources from different publishers reporting separately.",
                )
            )
    return findings


# ---------------------------------------------------------------------------
# The Evaluator
# ---------------------------------------------------------------------------
class Evaluator:
    name = "evaluator"

    def __init__(self, catalog, pool, trace, model=None, now=None, max_age_days=None):
        self.catalog = catalog
        self.pool = pool  # shared with the analyst: url -> Article
        self.trace = trace
        self.model = model or config.evaluator_model()
        self.expected_model = config.evaluator_expected() if model is None else model
        self.now = now
        self.max_age_days = max_age_days

    def evaluate(self, claim, retry_count=0):
        findings = deterministic_checks(claim, self.pool, self.catalog, self.now, self.max_age_days)
        cited = [self.pool[u] for u in dict.fromkeys(claim.source_urls) if u in self.pool]
        independent = len(independent_groups(cited)) if cited else 0

        if findings:
            verdict = Verdict("SEND_BACK", [f"{f.code}: {f.message}" for f in findings], independent, llm_checked=False)
            self._log(claim, verdict, retry_count, note="deterministic checks failed; model check skipped")
            return verdict

        supported, strength, reason = self._model_check(claim, cited, retry_count)
        if supported:
            verdict = Verdict("PASS", [reason], independent, strength, llm_checked=True)
        else:
            verdict = Verdict("SEND_BACK", [f"NOT_SUPPORTED: {reason}"], independent, strength, llm_checked=True)
        self._log(claim, verdict, retry_count)
        return verdict

    def _model_check(self, claim, cited, retry_count):
        articles = "\n".join(
            f"S{i} | {a.published[:10]} | {a.publisher} | {a.title}" + (f" - {a.summary[:300]}" if a.summary else "")
            for i, a in enumerate(cited, 1)
        )
        prompt = (
            f"Claim: {claim.claim}\n"
            f"Part: {claim.part_id} {claim.part_name}, supplied from {claim.supplier_country}.\n"
            f"Analyst says unit cost goes {'UP' if claim.direction == 'cost_up' else 'DOWN'} by "
            f"{claim.pct_low}-{claim.pct_high}% (analyst's own estimate).\n"
            f"Analyst reasoning: {claim.reasoning}\n\n"
            f"Cited sources:\n{articles}\n\n"
            "Do these sources, read together, support the claim (the development happened, it plausibly affects this "
            "part from this country, and the direction is right)? This is the GEOPOLITICS analyst: the driver must be a "
            "geopolitical or trade-policy development (export controls, sanctions, tariffs, tensions, trade deals). "
            "If the claim rests on market demand, energy or commodity prices or currency moves instead, answer "
            "supported=false and say it is out of scope. Reply exactly:\n"
            '{"supported": true or false, "strength": "strong" or "moderate" or "weak", "reason": "<one or two sentences>"}'
        )
        result = llm.chat(
            [{"role": "system", "content": EVALUATOR_SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
            model=self.model,
            expected_model=self.expected_model,
            agent=self.name,
            step="support-check",
            trace=self.trace,
            retry_count=retry_count,
        )
        try:
            data = llm.extract_json(result.text)
            return bool(data.get("supported")), str(data.get("strength", "moderate")).lower(), str(data.get("reason", "")).strip()
        except ValueError:
            # An unreadable audit is never a pass.
            return False, "weak", "The Evaluator's model reply could not be parsed, so the claim is not accepted."

    def _log(self, claim, verdict, retry_count, note=""):
        self.trace.event(
            self.name,
            "verdict",
            input_summary=f"{claim.claim_id} attempt {claim.attempt}: {claim.claim}",
            output_summary=" | ".join(verdict.reasons) + (f" ({note})" if note else ""),
            model=self.model if verdict.llm_checked else None,
            sources=claim.source_urls,
            decision=verdict.verdict,
            retry_count=retry_count,
            independent_sources=verdict.independent_sources,
        )


# ---------------------------------------------------------------------------
# The send-back loop
# ---------------------------------------------------------------------------
def audit_claim(claim, target, analyst, evaluator, trace, max_retries=MAX_RETRIES):
    """Evaluate a claim; on SEND_BACK return it to the analyst with the reason, up to max_retries times."""
    history = []
    retries = 0
    while True:
        verdict = evaluator.evaluate(claim, retry_count=retries)
        history.append({"attempt": claim.attempt, "claim": claim.to_dict(), "verdict": verdict.to_dict()})
        if verdict.verdict == "PASS":
            return AuditResult(claim, "PASS", verdict, retries, history)

        if retries >= max_retries:
            trace.event(evaluator.name, "final", input_summary=claim.claim_id, output_summary=f"still failing after {retries} retries",
                        decision="UNVERIFIED", retry_count=retries)
            return AuditResult(claim, "UNVERIFIED", verdict, retries, history)

        retries += 1
        trace.event(evaluator.name, "send-back", input_summary=claim.claim_id, output_summary=" | ".join(verdict.reasons),
                    decision="SEND_BACK", retry_count=retries)
        revised = analyst.revise_claim(target, claim, verdict.reasons)
        if revised is None:
            trace.event(analyst.name, "claim:withdrawn", input_summary=claim.claim_id,
                        output_summary="analyst found no adequate evidence after feedback", decision="UNVERIFIED", retry_count=retries)
            history.append({"attempt": claim.attempt + 1, "claim": None, "verdict": {"verdict": "WITHDRAWN", "reasons": ["Analyst withdrew the claim: no adequate evidence."]}})
            return AuditResult(claim, "UNVERIFIED", verdict, retries, history)
        claim = revised
