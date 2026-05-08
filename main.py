"""
DenialGPT -- FastMCP + FastAPI Server Entry Point

Registers MCP tools for denial analysis, clinical evidence fetching,
and gap analysis. Exposes /health and /.well-known/agent.json for
Railway deployment and Prompt Opinion marketplace registration.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastmcp import FastMCP

from middleware.sharp import SHARPContext
from fhir.client import FHIRClient
from tools.analyze_denial import run_analyze_denial

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(name)s | %(levelname)s | %(message)s")
logger = logging.getLogger("denialgpt")

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
        denial_type: One of: Medical Necessity, Coding Error, Missing Documentation, Untimely Filing.
        patient_id: FHIR Patient resource ID.
        date_of_service: ISO date string for the service in question.
        fhir_base_url: Override FHIR server URL (defaults to env/SHARP).
        access_token: Override bearer token (defaults to env/SHARP).

    Returns:
        Structured package of relevant FHIR resources.
    """
    from tools.fetch_evidence import run_fetch_clinical_evidence

    return await run_fetch_clinical_evidence(
        denial_type=denial_type,
        patient_id=patient_id,
        date_of_service=date_of_service,
        fhir_base_url=fhir_base_url,
        access_token=access_token,
    )


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
    from tools.gap_analysis import run_gap_analysis

    return await run_gap_analysis(
        denial_analysis=denial_analysis,
        clinical_evidence=clinical_evidence,
    )


# ---------------------------------------------------------------------------
# FastAPI wrapper -- health, agent card, MCP mount
# ---------------------------------------------------------------------------

app = FastAPI(title="DenialGPT", version="day3")


@app.get("/health")
async def health_check():
    """Health check endpoint for Railway and monitoring."""
    dev_mode = not all([
        os.getenv("FHIR_BASE_URL", "").startswith("https://"),
        os.getenv("DEV_ACCESS_TOKEN", "dev-token") != "dev-token",
    ])
    return {
        "status": "ok",
        "service": "DenialGPT",
        "version": "day3",
        "tools_registered": [
            "analyze_denial",
            "fetch_clinical_evidence",
            "gap_analysis",
        ],
        "dev_mode": dev_mode,
    }


AGENT_CARD_PATH = Path(__file__).resolve().parent / ".well-known" / "agent.json"


@app.get("/.well-known/agent.json")
async def agent_card():
    """Serve the A2A/MCP agent card for Prompt Opinion marketplace discovery."""
    if AGENT_CARD_PATH.exists():
        data = json.loads(AGENT_CARD_PATH.read_text(encoding="utf-8"))
        return JSONResponse(content=data, media_type="application/json")
    return JSONResponse(
        status_code=404,
        content={"error": "agent.json not found"},
    )


# Mount FastMCP as a sub-application under /mcp
app.mount("/mcp", mcp.http_app())


# ---------------------------------------------------------------------------
# Standalone run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
