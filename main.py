"""
DenialGPT — FastMCP Server Entry Point

Registers MCP tools for denial analysis, clinical evidence fetching,
and gap analysis. Tools are placeholders until Days 2–3.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from fastmcp import FastMCP

from middleware.sharp import SHARPContext
from fhir.client import FHIRClient
from tools.analyze_denial import run_analyze_denial

load_dotenv()

# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "DenialGPT",
    instructions=(
        "Denial Prevention & Gap Analysis Agent for hospital billing "
        "and revenue cycle teams."
    ),
)


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


@mcp.tool()
async def analyze_denial(
    denial_text: str,
    payer: str,
) -> dict:
    """
    Analyze a denial letter or FHIR ExplanationOfBenefit. Classifies denial type,
    extracts CARC codes, identifies payer objection, evidence required, appeal
    deadline, and performs root-cause analysis.

    Args:
        denial_text: The full text of the denial letter or serialized EOB JSON.
        payer: Payer name (e.g., "Aetna").

    Returns:
        Structured denial analysis with type, CARC codes, root_cause, and prevention.
    """
    return await run_analyze_denial(denial_text=denial_text, payer=payer)


# ---------------------------------------------------------------------------
# Placeholders — implementations land Day 3
# ---------------------------------------------------------------------------


@mcp.tool()
async def fetch_clinical_evidence(
    denial_type: str,
    patient_id: str,
    date_of_service: str,
    fhir_base_url: str | None = None,
    access_token: str | None = None,
) -> dict:
    """
    Fetch clinical evidence from FHIR sandbox based on denial type.

    Maps denial_type to the correct FHIR resources and retrieves them.

    Args:
        denial_type: One of: medical_necessity, coding_error, missing_documentation, untimely_filing.
        patient_id: FHIR Patient resource ID.
        date_of_service: ISO date string for the service in question.
        fhir_base_url: Override FHIR server URL (defaults to env/SHARP).
        access_token: Override bearer token (defaults to env/SHARP).

    Returns:
        Structured package of relevant FHIR resources.
    """
    return {
        "status": "not_implemented",
        "message": "fetch_clinical_evidence is scheduled for Day 3. Scaffold only.",
        "denial_type": denial_type,
        "patient_id": patient_id,
    }


@mcp.tool()
async def gap_analysis(
    denial_analysis: dict,
    clinical_evidence: dict,
) -> dict:
    """
    Compare payer requirements vs. clinical evidence to determine appeal viability.

    Args:
        denial_analysis: Output from analyze_denial tool.
        clinical_evidence: Output from fetch_clinical_evidence tool.

    Returns:
        evidence_found, evidence_missing, appeal_viability (STRONG/WEAK/DO NOT APPEAL),
        chain-of-thought reasoning, and next_steps.
    """
    return {
        "status": "not_implemented",
        "message": "gap_analysis is scheduled for Day 3. Scaffold only.",
    }


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
