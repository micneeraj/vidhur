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

from core import config, fx, llm
from core.claims import AuditResult, Verdict
from core.source_trust import publisher_key, trust_of

MAX_RETRIES = 2
SYNDICATION_TITLE_SIMILARITY = 0.88  # headlines this alike are treated as one story, not two sources
FX_TOLERANCE = 0.011  # percentage points; the analyst's range must equal the Python-computed moves
FX_RATE_TOLERANCE = 0.02  # a claimed FX rate may differ from the real rate for its date by at most 2%
FX_MOVE_TOLERANCE = 0.05  # percentage points; a claimed move must match the move recomputed from its own start and end rates
GBP_TOLERANCE = 0.10  # a GBP figure in a claim may differ from the calculator's result by at most 10%
EXCERPT_SIMILARITY = 0.9  # excerpts this alike are one report, not two sources
MIN_EXCERPT_CHARS = 40

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


_ORIGIN_RE = re.compile(
    r"(?:\bvia\b|originally (?:published|reported) (?:by|in)|\bsource:|\bwith (?:reporting|files) from|\bsyndicated from|"
    r"\breprinted from|\bcourtesy of)\s+([A-Z][\w&\-]*(?:\.\w+)*(?: [A-Z][\w&\-]*(?:\.\w+)*){0,3})")


def original_outlet(article):
    """The outlet a story originally came from: a 'via X' / 'originally reported by X' credit in the text, else its own publisher."""
    m = _ORIGIN_RE.search(f"{article.title} {article.summary or ''}")
    return _norm_publisher(m.group(1)) if m else _norm_publisher(article.publisher)


def duplicate_reason(a, b):
    """Why two articles count as ONE source (same outlet, same original outlet, same headline, near-same excerpt), or None."""
    if (_norm_publisher(a.publisher) == _norm_publisher(b.publisher)
            or publisher_key(a.url, a.publisher) == publisher_key(b.url, b.publisher)):
        return "same outlet"
    if original_outlet(a) in (original_outlet(b), _norm_publisher(b.publisher)) or original_outlet(b) == _norm_publisher(a.publisher):
        return "same original outlet"
    if SequenceMatcher(None, _norm_title(a.title), _norm_title(b.title)).ratio() >= SYNDICATION_TITLE_SIMILARITY:
        return "same headline"
    sa, sb = _norm_title(a.summary or ""), _norm_title(b.summary or "")
    if len(sa) >= MIN_EXCERPT_CHARS and len(sb) >= MIN_EXCERPT_CHARS and SequenceMatcher(None, sa, sb).ratio() >= EXCERPT_SIMILARITY:
        return "near-identical excerpt"
    return None


def independent_groups(articles):
    """Group articles that are the same outlet, the same original outlet or the same syndicated story.

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
            if duplicate_reason(articles[i], articles[j]):
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


# ---- figures in a claim: % and money amounts --------------------------------------------------------------
_NUM = r"\d[\d,]*(?:\.\d+)?"
_SCENARIO_TAG = r"(\s*\(SCENARIO\))?"
PCT_RE = re.compile(rf"(?<![\w.])({_NUM})\s?(?:%|percent\b|per cent\b)" + _SCENARIO_TAG, re.I)
MONEY_RE = re.compile(rf"(£|\bGBP\b|\bUSD\b|US\$|\$|€|\bEUR\b)\s?({_NUM})\s?(bn|billion|mn|million|m|k|thousand)?(?![a-z])" + _SCENARIO_TAG, re.I)
MULTIPLIER = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}


def _to_float(token):
    return float(token.replace(",", "").rstrip("."))


def claim_figures(text):
    """Figures stated in a claim: {"percents": [(value, scenario_tagged)], "money": [(currency, value, scenario_tagged)]}.

    A figure followed by "(SCENARIO)" is declared to be our own assumption and is not checked against sources.
    """
    text = text or ""
    percents = [(_to_float(m.group(1)), bool(m.group(2))) for m in PCT_RE.finditer(text)]
    money = []
    for m in MONEY_RE.finditer(text):
        cur = m.group(1).upper().replace("£", "GBP").replace("US$", "USD").replace("$", "USD").replace("€", "EUR")
        value = _to_float(m.group(2)) * MULTIPLIER.get((m.group(3) or "").lower(), 1)
        money.append((cur, value, bool(m.group(4))))
    return {"percents": percents, "money": money}


def numbers_in(text):
    return [_to_float(t) for t in re.findall(_NUM, text or "")]


def _appears(value, numbers):
    """True if `value` is among the numbers of a source text (a rounded quote such as 3.5 for 3.52 counts)."""
    text = f"{value:.10g}"
    decimals = len(text.split(".")[1]) if "." in text else 0
    return any(abs(n - value) < 1e-9 or round(n, decimals) == value for n in numbers)


def _excerpt_numbers(known, claim):
    text = " ".join(f"{a.title} {a.summary or ''}" for a in known) + " " + " ".join(str(e.get("excerpt", "")) for e in claim.evidence or [])
    return numbers_in(text)


def number_findings(claim, known):
    """NUMBER_NOT_IN_SOURCE: every % or figure in the claim must appear in a cited excerpt, or be tagged (SCENARIO).

    GBP amounts are not checked here: GBP_MISMATCH compares them with the calculator. A structured percentage range labelled
    FORECAST ('a source states it') must also appear in the excerpts.
    """
    nums = _excerpt_numbers(known, claim)
    fig = claim_figures(claim.claim)
    missing = [f"{v:g}%" for v, tagged in fig["percents"] if not tagged and not _appears(v, nums)]
    missing += [f"{cur} {v:,.0f}" for cur, v, tagged in fig["money"] if cur != "GBP" and not tagged and not _appears(v, nums)]
    if (claim.pct_label or "").upper() == "FORECAST":
        missing += [f"{v:g}% (stated range)" for v in (claim.pct_low, claim.pct_high) if not _appears(v, nums)]
    if not missing:
        return []
    return [Finding("NUMBER_NOT_IN_SOURCE", f"The claim states {', '.join(dict.fromkeys(missing))}, which does not appear in any cited "
                    "excerpt. Quote only figures that a cited source prints, or tag your own assumption (SCENARIO).")]


def currency_country_findings(claim):
    """CURRENCY_COUNTRY_MISMATCH: the currency a claim is about must be the supplier country's (Taiwan: TWD, with USD as proxy)."""
    cur = (claim.currency or (claim.computed or {}).get("currency") or "").upper()
    allowed = fx.COUNTRY_CURRENCIES.get(claim.supplier_country)
    invoice = ((claim.computed or {}).get("invoice_currency") or "").upper()
    if invoice:  # Zetaris prices the part in this currency, so it is the exposure whatever the supplier's home currency
        allowed = {*(allowed or ()), invoice}
    if cur and allowed and cur not in allowed:
        return [Finding("CURRENCY_COUNTRY_MISMATCH", f"{cur} is not the currency of {claim.supplier_country} "
                        f"({'/'.join(sorted(allowed))}); the claim must use the supplier country's currency.")]
    return []


def fx_pair_findings(claim, calc):
    """FX_PAIR_MISMATCH: the FX pair of a currency claim must be the currency of the Zetaris row the GBP calculation used."""
    cur = (claim.currency or (claim.computed or {}).get("currency") or "").upper()
    priced = ((calc or {}).get("fx") or {}).get("currency", "").upper()
    if cur and priced and cur != priced:
        return [Finding("FX_PAIR_MISMATCH", f"The claim is about GBP/{cur} but Ganga's Zetaris row for {claim.part_id} is priced in "
                        f"{priced}, so the calculation used GBP/{priced}; the FX pair must match the row's currency.")]
    return []


def fx_rate_findings(claim, fx_rate_fn):
    """FX_MISMATCH (rates): the claimed start and end rates must match the real ECB rates for their dates within 2%, and the claimed
    90-day move must follow from those two rates. Fails closed: a rate that cannot be checked is not accepted."""
    c = claim.computed or {}
    cur = (c.get("currency") or claim.currency or "").upper()
    if not cur or "end_rate" not in c:
        return []
    out = []
    for which in ("start", "end"):
        claimed, day = c.get(f"{which}_rate"), c.get(f"{which}_date")
        if claimed is None or not day:
            continue
        try:
            real, real_day = fx_rate_fn(cur, day)
        except Exception as exc:
            out.append(Finding("FX_MISMATCH", f"The {which} rate for {cur} on {day} could not be checked against ECB data "
                               f"({type(exc).__name__}); not accepted unchecked."))
            continue
        if not real or abs(float(claimed) / real - 1) > FX_RATE_TOLERANCE:
            out.append(Finding("FX_MISMATCH", f"Claimed GBP/{cur} rate {claimed} on {day} differs from the real ECB rate {real} "
                               f"({real_day}) by more than {FX_RATE_TOLERANCE:.0%}."))
    try:
        move = fx.pct_change_in_gbp_cost(float(c["start_rate"]), float(c["end_rate"]))
        if abs(move - float(c["move_90d_pct"])) > FX_MOVE_TOLERANCE:
            out.append(Finding("FX_MISMATCH", f"The claimed 90-day move {float(c['move_90d_pct']):+.2f}% does not follow from its own "
                               f"rates (they give {move:+.2f}%)."))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass
    return out


def gbp_findings(claim, calc):
    """GBP_MISMATCH: any GBP figure in the claim must be within 10% of a calculator.py result for this part
    (baseline spend, or the worst / likely / best change). With no calculator result the figure cannot be verified."""
    figures = [v for cur, v, _ in claim_figures(claim.claim)["money"] if cur == "GBP"]
    if not figures:
        return []
    if not calc:
        return [Finding("GBP_MISMATCH", "The claim states a GBP figure but no calculator result is available to check it; "
                        "remove GBP figures from the claim (the calculator produces them).")]
    reference = {"baseline": calc["baseline_gbp"], **{k: abs(v) for k, v in calc["result_gbp"].items() if k in ("worst", "likely", "best")}}
    out = []
    for v in figures:
        if not any(r and abs(v - r) / r <= GBP_TOLERANCE for r in reference.values()):
            listing = ", ".join(f"{k} £{r:,.0f}" for k, r in reference.items())
            out.append(Finding("GBP_MISMATCH", f"The claim states GBP {v:,.0f}; the calculator gives {listing} "
                               f"(tolerance {GBP_TOLERANCE:.0%}). GBP figures come from the calculator, not from the analyst."))
    return out


def duplicate_findings(known):
    """DUPLICATE_SOURCE: cited sources that are the same outlet, same original outlet, or carry near-identical text count as one;
    fewer than two independent sources remain."""
    if len(known) < 2 or len(independent_groups(known)) >= 2:
        return []
    reasons = sorted({r for i, a in enumerate(known) for b in known[i + 1:] if (r := duplicate_reason(a, b))})
    names = sorted({a.publisher for a in known})
    return [Finding("DUPLICATE_SOURCE", f"The cited sources count as one ({', '.join(reasons) or 'duplicates'}; publishers: "
                    f"{', '.join(names)}). Cite sources from different outlets reporting separately.")]


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


def deterministic_checks(claim, pool, catalog, now=None, max_age_days=None, calc=None, fx_rate_fn=None):
    """Return a list of Findings (empty list = all deterministic checks passed).

    pool:    {url: Article} - everything actually fetched this run
    catalog: {part_id: {"part_name", "country"}} - Ganga's parts, read from Zetaris
    calc:    the calculator.py calculation record for this claim's part (to check GBP figures), or None
    fx_rate_fn: (currency, date) -> (units per GBP, date); real ECB data by default
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

    findings.extend(duplicate_findings(known))

    # 3b. trust layer: only fresh sources count; one of them must be Tier 1 or 2; copies of one report count once
    findings.extend(trust_findings(known, now, max_age_days))

    # 4. analyst-specific consistency with computed data
    if claim.analyst == "currency":
        findings.extend(_currency_consistency(claim))

    # 5. numbers: every figure is traceable to a source, the data or the calculator
    findings.extend(number_findings(claim, known))
    findings.extend(currency_country_findings(claim))
    findings.extend(fx_pair_findings(claim, calc))
    findings.extend(fx_rate_findings(claim, fx_rate_fn or fx.rate_on))
    findings.extend(gbp_findings(claim, calc))
    return findings


CHECK_GROUPS = [
    ("Part and country exist in Ganga's Zetaris data", {"UNKNOWN_PART", "COUNTRY_MISMATCH", "UNKNOWN_COUNTRY"}),
    ("Direction and % range are well formed", {"BAD_DIRECTION", "BAD_RANGE"}),
    ("At least 2 sources, all of them actually fetched", {"TOO_FEW_SOURCES", "UNKNOWN_URL"}),
    ("Sources are fresh", {"STALE_SOURCE", "STALE_SOURCES"}),
    ("Sources are independent and one is Tier 1 or 2", {"DUPLICATE_SOURCE", "NO_TRUSTED_SOURCE", "SOURCES_NOT_INDEPENDENT"}),
    ("Every number traces to a source, the data or the calculator", {"NUMBER_NOT_IN_SOURCE", "GBP_MISMATCH"}),
    ("Currency and FX figures match ECB data", {"CURRENCY_COUNTRY_MISMATCH", "FX_MISMATCH", "FX_NO_DATA"}),
]


def check_results(findings):
    """The deterministic checks as [{name, ok, detail}] - one row per check group, with the findings that failed it."""
    rows = []
    for name, codes in CHECK_GROUPS:
        failed = [f for f in findings if f.code in codes]
        rows.append({"name": name, "ok": not failed, "detail": " | ".join(f"{f.code}: {f.message}" for f in failed)})
    other = [f for f in findings if not any(f.code in codes for _, codes in CHECK_GROUPS)]
    if other:
        rows.append({"name": "Other checks", "ok": False, "detail": " | ".join(f"{f.code}: {f.message}" for f in other)})
    return rows


# ---------------------------------------------------------------------------
# The Evaluator
# ---------------------------------------------------------------------------
class Evaluator:
    name = "evaluator"

    def __init__(self, catalog, pool, world, trace, model=None, now=None, max_age_days=None, calc_fn=None, fx_rate_fn=None):
        self.catalog = catalog
        self.pool = pool  # shared with the analysts: url -> Article
        self.world = world  # core.shared_state.RunWorld: claims are read from it, verdicts written to it
        self.trace = trace
        self.model = model or config.evaluator_model()
        self.expected_model = config.evaluator_expected() if model is None else model
        self.now = now
        self.max_age_days = max_age_days
        self.calc_fn = calc_fn  # claim -> Impact from calculator.py; its record is what GBP figures in a claim are checked against
        self.fx_rate_fn = fx_rate_fn

    def evaluate(self, claim_key, retry_count=0):
        claim = self.world.get_claim(claim_key)  # read from the shared world state, not from a local variable
        calc = self.calc_fn(claim).calc if self.calc_fn else None
        findings = deterministic_checks(claim, self.pool, self.catalog, self.now, self.max_age_days, calc=calc, fx_rate_fn=self.fx_rate_fn)
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
        verdict.checks = check_results(findings)
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
