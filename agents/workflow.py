"""One unit of delegated work: an analyst drafts a claim on a part, the Evaluator audits it, GBP impact is computed."""

import openai
import requests

from agents.evaluator import audit_claim
from core.calculator import CalcService
from core.impact import confidence_level
from core.zetaris_client import ZetarisError


def run_work_item(delegation, target, analysts, evaluator, world, zt, trace, calc=None):
    """Runs inside a worker thread. Everything it learns is written to the world state; nothing is returned.

    calc: the core.calculator.CalcService that produces every GBP figure (one is built from Zetaris if none is given)."""
    calc = calc or CalcService(zt, target.quarter)
    work_id = delegation["work_id"]
    with trace.target(work_id):
        trace.event("workflow", "start", input_summary=f"{delegation['analyst']} on {target.part_id} {target.country}",
                    output_summary=delegation.get("reason", ""))
        world.set_delegation_status(work_id, "running", "workflow")
        try:
            analyst = analysts[delegation["analyst"]]
            claim = analyst.draft(target, work_id)
            if claim is None:
                note = getattr(analyst, "notes", {}).get(work_id) or "the analyst found no supported development"
                world.set_delegation_status(work_id, "NO_CLAIM", "workflow", note=note)
                trace.event("workflow", "no-claim", output_summary=note, decision="NO_CLAIM")
                return
            result = audit_claim(world.latest_claim_key(work_id), target, work_id, analyst, evaluator, world, trace)
            if result.status == "PASS":
                claim = world.get_claim(result.claim_key)
                impact = calc.for_claim(claim)  # GBP is calculated here in Python from Zetaris rows + a dated FX rate
                spend, quarter = impact.baseline_quarterly_spend_gbp, calc.quarter
                confidence = confidence_level(result.verdict.independent_sources, result.retries, result.verdict.strength)
                world.write_impact(work_id, result.claim_key, impact, confidence, quarter)
                trace.event("impact", "gbp", input_summary=f"{claim.part_id} {quarter} spend £{spend:,.2f}",
                            output_summary=f"scenario range worst {impact.worst_gbp:+,.0f} / likely {impact.likely_gbp:+,.0f} / best {impact.best_gbp:+,.0f} "
                                           f"({impact.pct_label} {impact.pct_low:g}-{impact.pct_high:g}%; FX {impact.calc['fx']['units_per_gbp']} on "
                                           f"{impact.calc['fx']['date']}); confidence {confidence}", decision="PASS")
        except (openai.OpenAIError, ZetarisError, requests.RequestException) as exc:
            # A model outage on one work item must not lose the others; it is reported, never hidden.
            message = f"{type(exc).__name__}: {str(exc)[:200]}"
            world.write_error(work_id, message)
            trace.event("workflow", "error", output_summary=message, decision="NOT_ASSESSED")
