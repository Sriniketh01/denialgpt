"""
DenialGPT LangGraph Orchestration Agent (Day 4)

Two entry points:
  Workflow A — Prevention:  check_claim_policy  (Person B's tool)
  Workflow B — Gap Analysis: analyze_denial → fetch_clinical_evidence → gap_analysis

Both workflows are simple linear graphs. No complex branching per spec.

Usage:
    import asyncio
    from agent.workflow import run_gap_analysis_workflow, run_prevention_workflow

    # Workflow B — full gap analysis chain
    result = asyncio.run(run_gap_analysis_workflow(
        denial_text="Claim denied. CARC 50...",
        payer="Aetna",
        patient_id="patient-uuid",
        date_of_service="2026-05-02",
        fhir_base_url="https://fhir.example.com/r4",
        access_token="bearer-token",
    ))
    print(result["gap_result"]["appeal_viability"])  # STRONG / WEAK / DO NOT APPEAL

    # Workflow A — pre-submission prevention check
    result = asyncio.run(run_prevention_workflow(
        claim_data={"cpt": "71046", "icd10": "M17.11", "payer": "Aetna"},
        patient_id="patient-uuid",
    ))
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from tools.analyze_denial import run_analyze_denial
from tools.fetch_evidence import run_fetch_clinical_evidence
from tools.gap_analysis import run_gap_analysis

logger = logging.getLogger("denialgpt.agent")


# ---------------------------------------------------------------------------
# Shared state schema
# ---------------------------------------------------------------------------

class DenialState(TypedDict, total=False):
    # ── Inputs ────────────────────────────────────────────────────────────
    denial_text: str
    payer: str
    patient_id: str | None
    date_of_service: str | None
    fhir_base_url: str | None
    access_token: str | None

    # ── Intermediate ──────────────────────────────────────────────────────
    denial_analysis: dict[str, Any] | None
    clinical_evidence: dict[str, Any] | None

    # ── Output ────────────────────────────────────────────────────────────
    gap_result: dict[str, Any] | None
    prevention_result: dict[str, Any] | None
    error: str | None


class PreventionState(TypedDict, total=False):
    # ── Inputs ────────────────────────────────────────────────────────────
    claim_data: dict[str, Any]   # {cpt, icd10, payer, patient_id, ...}
    patient_id: str | None
    fhir_base_url: str | None
    access_token: str | None

    # ── Output ────────────────────────────────────────────────────────────
    prevention_result: dict[str, Any] | None
    error: str | None


# ---------------------------------------------------------------------------
# Workflow B nodes — Gap Analysis chain
# ---------------------------------------------------------------------------

async def _node_analyze_denial(state: DenialState) -> DenialState:
    """Step 1: Classify the denial and extract structured metadata."""
    logger.info("workflow_b node=analyze_denial")
    try:
        result = await run_analyze_denial(
            denial_text=state.get("denial_text", ""),
            payer=state.get("payer", ""),
        )
        logger.info(
            "analyze_denial done denial_type=%s carc=%s",
            result.get("denial_type"), result.get("carc_code"),
        )
        return {**state, "denial_analysis": result}
    except Exception as exc:
        logger.exception("node_analyze_denial failed")
        return {**state, "error": str(exc)}


async def _node_fetch_evidence(state: DenialState) -> DenialState:
    """Step 2: Retrieve relevant FHIR resources for the denial type."""
    if state.get("error"):
        return state

    denial = state.get("denial_analysis") or {}
    denial_type = denial.get("denial_type")
    patient_id = state.get("patient_id")

    if not patient_id:
        logger.warning("node_fetch_evidence skipped — no patient_id in state")
        return {**state, "clinical_evidence": {"evidence": {}, "resources_fetched": [],
                                                "fhir_resource_ids": [], "fetch_errors": [],
                                                "summary": "No patient ID — FHIR fetch skipped."}}

    logger.info("workflow_b node=fetch_evidence denial_type=%s patient=%s",
                denial_type, patient_id)
    try:
        result = await run_fetch_clinical_evidence(
            denial_type=denial_type or "Medical Necessity",
            patient_id=patient_id,
            date_of_service=state.get("date_of_service") or "",
            fhir_base_url=state.get("fhir_base_url"),
            access_token=state.get("access_token"),
        )
        logger.info("fetch_evidence done resources=%s", result.get("resources_fetched"))
        return {**state, "clinical_evidence": result}
    except Exception as exc:
        logger.exception("node_fetch_evidence failed")
        return {**state, "error": str(exc)}


async def _node_gap_analysis(state: DenialState) -> DenialState:
    """Step 3: Compare payer requirements vs. clinical evidence → verdict."""
    if state.get("error"):
        return state

    logger.info("workflow_b node=gap_analysis")
    try:
        result = await run_gap_analysis(
            denial_analysis=state.get("denial_analysis") or {},
            clinical_evidence=state.get("clinical_evidence") or {},
        )
        logger.info("gap_analysis done viability=%s", result.get("appeal_viability"))
        return {**state, "gap_result": result}
    except Exception as exc:
        logger.exception("node_gap_analysis failed")
        return {**state, "error": str(exc)}


# ---------------------------------------------------------------------------
# Workflow A node — Prevention (check_claim_policy)
# ---------------------------------------------------------------------------

async def _node_check_claim_policy(state: PreventionState) -> PreventionState:
    """
    Workflow A: Pre-submission policy check via Person B's check_claim_policy tool.

    Builds a ClaimDraft from state["claim_data"] and calls run_check_claim_policy.
    Expected keys in claim_data: cpt_code, icd10_code, payer, place_of_service,
    procedure_description.
    """
    from prevention.check_claim_policy import ClaimDraft, run_check_claim_policy

    logger.info("workflow_a node=check_claim_policy")
    try:
        claim_data = state.get("claim_data") or {}
        claim = ClaimDraft(
            cpt_code=claim_data.get("cpt_code", claim_data.get("cpt", "")),
            icd10_code=claim_data.get("icd10_code", claim_data.get("icd10", "")),
            payer=claim_data.get("payer", "Aetna"),
            place_of_service=claim_data.get("place_of_service", "outpatient"),
            procedure_description=claim_data.get("procedure_description", ""),
        )
        result = await run_check_claim_policy(claim)
        logger.info(
            "check_claim_policy done risk=%s flags=%d",
            result.overall_risk,
            len(result.risk_flags),
        )
        return {**state, "prevention_result": result.model_dump()}
    except Exception as exc:
        logger.exception("node_check_claim_policy failed")
        return {**state, "error": str(exc)}


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def _build_gap_analysis_graph() -> StateGraph:
    """
    Workflow B: analyze_denial → fetch_clinical_evidence → gap_analysis

    Linear graph — no branching per spec.
    """
    g = StateGraph(DenialState)

    g.add_node("analyze_denial",   _node_analyze_denial)
    g.add_node("fetch_evidence",   _node_fetch_evidence)
    g.add_node("gap_analysis",     _node_gap_analysis)

    g.add_edge(START,            "analyze_denial")
    g.add_edge("analyze_denial", "fetch_evidence")
    g.add_edge("fetch_evidence", "gap_analysis")
    g.add_edge("gap_analysis",   END)

    return g


def _build_prevention_graph() -> StateGraph:
    """
    Workflow A: check_claim_policy (single-node linear graph)

    Expanded by Person B once check_claim_policy tool is available.
    """
    g = StateGraph(PreventionState)

    g.add_node("check_claim_policy", _node_check_claim_policy)

    g.add_edge(START,                "check_claim_policy")
    g.add_edge("check_claim_policy", END)

    return g


# Compiled graphs — reused across calls (no state carried between invocations)
gap_analysis_graph   = _build_gap_analysis_graph().compile()
prevention_graph     = _build_prevention_graph().compile()


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

async def run_gap_analysis_workflow(
    denial_text: str,
    payer: str,
    patient_id: str | None = None,
    date_of_service: str | None = None,
    fhir_base_url: str | None = None,
    access_token: str | None = None,
) -> DenialState:
    """
    Workflow B — Gap Analysis.

    Runs: analyze_denial → fetch_clinical_evidence → gap_analysis.

    Args:
        denial_text:      Full text of the denial letter or serialized EOB JSON.
        payer:            Payer name, e.g. "Aetna".
        patient_id:       FHIR Patient resource ID (skip FHIR fetch if None).
        date_of_service:  ISO date string, e.g. "2026-05-02".
        fhir_base_url:    FHIR server base URL (falls back to env).
        access_token:     Bearer token for FHIR (falls back to env).

    Returns:
        Final DenialState with gap_result populated (or error set on failure).
    """
    initial: DenialState = {
        "denial_text":     denial_text,
        "payer":           payer,
        "patient_id":      patient_id,
        "date_of_service": date_of_service,
        "fhir_base_url":   fhir_base_url,
        "access_token":    access_token,
        "denial_analysis": None,
        "clinical_evidence": None,
        "gap_result":      None,
        "error":           None,
    }
    logger.info("workflow_b start patient_id=%s payer=%s", patient_id, payer)
    final: DenialState = await gap_analysis_graph.ainvoke(initial)  # type: ignore[arg-type]
    if final.get("error"):
        logger.error("workflow_b finished with error: %s", final["error"])
    else:
        viability = (final.get("gap_result") or {}).get("appeal_viability", "unknown")
        logger.info("workflow_b finished viability=%s", viability)
    return final


async def run_prevention_workflow(
    claim_data: dict[str, Any],
    patient_id: str | None = None,
    fhir_base_url: str | None = None,
    access_token: str | None = None,
) -> PreventionState:
    """
    Workflow A — Prevention (pre-submission policy check).

    Delegates to check_claim_policy (Person B's tool).
    Stub returns a placeholder result until cross-lane dependency is merged.

    Args:
        claim_data:    Dict with claim metadata: {cpt, icd10, payer, ...}
        patient_id:    FHIR Patient resource ID.
        fhir_base_url: FHIR server base URL.
        access_token:  Bearer token for FHIR.

    Returns:
        Final PreventionState with prevention_result populated.
    """
    initial: PreventionState = {
        "claim_data":   claim_data,
        "patient_id":   patient_id,
        "fhir_base_url": fhir_base_url,
        "access_token": access_token,
        "prevention_result": None,
        "error": None,
    }
    logger.info("workflow_a start patient_id=%s claim=%s", patient_id, claim_data)
    final: PreventionState = await prevention_graph.ainvoke(initial)  # type: ignore[arg-type]
    logger.info("workflow_a finished")
    return final
