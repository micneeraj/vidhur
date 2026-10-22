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

    def to_dict(self):
        return asdict(self)


@dataclass
class AuditResult:
    claim_key: str  # the final claim's key in the world state
    status: str  # "PASS" or "UNVERIFIED"
    verdict: Verdict
    retries: int
