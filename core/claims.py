"""The claim an analyst makes and the audit record the Evaluator produces."""

from dataclasses import asdict, dataclass, field


@dataclass
class Claim:
    claim_id: str
    claim: str
    part_id: str
    part_name: str
    supplier_country: str
    direction: str  # "cost_up" or "cost_down"
    pct_low: float  # estimated % change in unit cost (magnitude)
    pct_high: float
    source_urls: list = field(default_factory=list)  # urls cited; the Evaluator checks each one exists
    reasoning: str = ""
    attempt: int = 1
    analyst: str = "geopolitics"
    computed: dict = field(default_factory=dict)  # numbers computed in Python (e.g. FX moves), checked by the Evaluator
    currency: str = ""  # the currency the claim's driver is about (currency analyst); checked against the supplier country
    driver: str = ""  # the development behind the claim, in a few words
    evidence: list = field(default_factory=list)  # [{url, publisher, date, excerpt}] - filled in Python from the fetched article
    pct_label: str = ""  # how pct_low / pct_high are labelled: FORECAST (a source states it), OBSERVED (computed from data), SCENARIO (our assumption)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Verdict:
    verdict: str  # "PASS" or "SEND_BACK"
    reasons: list = field(default_factory=list)
    independent_sources: int = 0
    strength: str = ""  # strong / moderate / weak, from the Evaluator's model check
    llm_checked: bool = False
    checks: list = field(default_factory=list)  # deterministic check results: [{name, ok, detail}]

    def to_dict(self):
        return asdict(self)


@dataclass
class AuditResult:
    claim_key: str  # the final claim's key in the world state
    status: str  # "PASS" or "UNVERIFIED"
    verdict: Verdict
    retries: int
