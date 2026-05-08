"""
DenialGPT -- FastMCP + FastAPI Server Entry Point (A2A v1)

Exposes:
  POST /                            A2A v1 agent endpoint (Prompt Opinion)
  GET  /.well-known/agent-card.json A2A v1 agent card (public)
  GET  /.well-known/agent.json      Legacy agent card (kept for compat)
  GET  /health                      Health check
  /mcp                              FastMCP (dev/testing)

PO-specific notes
─────────────────
  • PO sends PascalCase method names: SendMessage / SendStreamingMessage
    → normalized to message/send before processing.
  • PO sends proto-style roles: ROLE_USER → user.
  • PO expects Content-Type: application/a2a+json in responses.
  • PO expects status.state as proto enum: TASK_STATE_COMPLETED.
  • PO expects artifact parts WITHOUT a "kind" field.
  • Response shape: {"jsonrpc":"2.0","id":...,"result":{"task":{...}}}
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastmcp import FastMCP

from middleware.sharp import SHARPContext
from tools.analyze_denial import run_analyze_denial

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(name)s | %(levelname)s | %(message)s")
logger = logging.getLogger("denialgpt")

# ---------------------------------------------------------------------------
# A2A protocol constants
# ---------------------------------------------------------------------------

# PO sends these PascalCase method names — normalize to A2A spec names.
_METHOD_ALIASES: dict[str, str] = {
    "SendMessage":          "message/send",
    "SendStreamingMessage": "message/send",
    "GetTask":              "tasks/get",
    "CancelTask":           "tasks/cancel",
}

# PO sends proto-style role names.
_ROLE_ALIASES: dict[str, str] = {
    "ROLE_USER":  "user",
    "ROLE_AGENT": "agent",
}


# ---------------------------------------------------------------------------
# API key helpers
# ---------------------------------------------------------------------------

def _load_api_keys() -> set[str]:
    """Load valid API keys from env: API_KEYS (comma-sep), API_KEY_PRIMARY, API_KEY_SECONDARY."""
    keys: set[str] = set()
    raw = os.getenv("API_KEYS", "").strip()
    if raw:
        keys.update(k.strip() for k in raw.split(",") if k.strip())
    for name in ("API_KEY_PRIMARY", "API_KEY_SECONDARY"):
        v = os.getenv(name, "").strip()
        if v:
            keys.add(v)
    return keys


def _check_api_key(request: Request) -> bool:
    """Return True if request is authorized (or no keys configured)."""
    valid = _load_api_keys()
    if not valid:
        return True  # no key configured → open access
    return request.headers.get("X-API-Key", "") in valid


# ---------------------------------------------------------------------------
# Agent card builder (A2A v1)
# ---------------------------------------------------------------------------

def _build_agent_card() -> dict[str, Any]:
    base_url = os.getenv("AGENT_BASE_URL", "https://denialgpt.onrender.com").rstrip("/")
    po_base  = os.getenv("PO_PLATFORM_BASE_URL", "https://app.promptopinion.ai").rstrip("/")
    fhir_ext = f"{po_base}/schemas/a2a/v1/fhir-context"

    api_keys = _load_api_keys()
    if api_keys:
        security_schemes: Any = {
            "apiKey": {
                "apiKeySecurityScheme": {
                    "name": "X-API-Key",
                    "location": "header",
                    "description": "API key required to access DenialGPT.",
                }
            }
        }
        security: Any = [{"apiKey": []}]
    else:
        security_schemes = None
        security = None

    card: dict[str, Any] = {
        "name": "DenialGPT",
        "description": (
            "Denial Prevention and Gap Analysis Agent for hospital revenue cycle teams. "
            "Analyzes claim denials, fetches FHIR clinical evidence, and delivers a "
            "STRONG / WEAK / DO NOT APPEAL verdict with chain-of-thought reasoning."
        ),
        "url": base_url,           # kept for a2a-sdk compat; real endpoint in supportedInterfaces
        "version": "1.0.0",
        "defaultInputModes":  ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": False,
            "extensions": [
                {
                    "uri": fhir_ext,
                    "description": "FHIR R4 context for querying patient clinical records.",
                    "required": False,
                    "params": {
                        "scopes": [
                            {"name": "patient/Condition.rs",         "required": True},
                            {"name": "patient/Procedure.rs",         "required": True},
                            {"name": "patient/MedicationRequest.rs", "required": True},
                            {"name": "patient/DocumentReference.rs", "required": False},
                            {"name": "patient/Observation.rs",       "required": False},
                            {"name": "patient/ExplanationOfBenefit.rs", "required": False},
                        ]
                    },
                }
            ],
        },
        # A2A v1: replaces top-level url + preferredTransport
        "supportedInterfaces": [
            {"url": base_url, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
        ],
        "skills": [
            {
                "id": "analyze_denial",
                "name": "analyze_denial",
                "description": (
                    "Classifies a claim denial, extracts CARC codes, identifies required "
                    "evidence, calculates appeal deadline, and diagnoses root cause."
                ),
                "tags": ["denial", "billing", "revenue-cycle"],
            },
            {
                "id": "fetch_clinical_evidence",
                "name": "fetch_clinical_evidence",
                "description": (
                    "Queries the FHIR sandbox for patient clinical records relevant to "
                    "the denial type using SHARP context headers."
                ),
                "tags": ["fhir", "clinical", "evidence"],
            },
            {
                "id": "gap_analysis",
                "name": "gap_analysis",
                "description": (
                    "Compares payer requirements against FHIR clinical evidence. "
                    "Returns STRONG / WEAK / DO NOT APPEAL verdict with chain-of-thought reasoning."
                ),
                "tags": ["gap-analysis", "appeal", "revenue-cycle"],
            },
        ],
    }

    if security_schemes:
        card["securitySchemes"] = security_schemes
    if security:
        card["security"] = security

    return card


# ---------------------------------------------------------------------------
# FHIR context extraction
# ---------------------------------------------------------------------------

def _extract_fhir_context(metadata: dict) -> dict[str, Any] | None:
    """Find the FHIR context dict under any key ending in /fhir-context."""
    for key, value in metadata.items():
        if "fhir-context" in key.lower() and isinstance(value, dict):
            return value
    return None


# ---------------------------------------------------------------------------
# Response formatters
# ---------------------------------------------------------------------------

def _format_full_result(denial: dict, gap: dict) -> str:
    rc = denial.get("root_cause", {})
    viability = gap.get("appeal_viability", "")
    icon = {"STRONG": "✅", "WEAK": "⚠️", "DO NOT APPEAL": "❌"}.get(viability, "")

    lines = [
        "## DenialGPT Analysis",
        "",
        f"**Denial Type:** {denial.get('denial_type')}",
        f"**CARC Code:** {denial.get('carc_code')}",
        f"**Payer Reason:** {denial.get('payer_stated_reason')}",
        f"**Appeal Deadline:** {denial.get('appeal_deadline')}",
        "",
        "### Root Cause",
        f"**Category:** {rc.get('category')}",
        f"{rc.get('explanation', '')}",
        f"**Prevention:** {rc.get('prevention', '')}",
        "",
        f"### Appeal Viability: {viability} {icon}",
        "",
        f"**Reasoning:** {gap.get('reasoning', '')}",
        "",
        "**Evidence Found:**",
    ]
    for item in gap.get("evidence_found", []):
        lines.append(f"  - {item}")

    lines += ["", "**Evidence Missing:**"]
    for item in gap.get("evidence_missing", []):
        lines.append(f"  - {item}")

    lines += ["", "**Next Steps:**"]
    for step in gap.get("next_steps", []):
        lines.append(f"  - {step}")

    # Write-off memo (only on DO NOT APPEAL)
    memo = gap.get("writeoff_memo")
    if viability == "DO NOT APPEAL" and memo:
        lines += [
            "",
            "---",
            "### Write-Off Memo",
            f"**Patient:** {memo.get('patient')}",
            f"**Denial Date:** {memo.get('denial_date')}",
            f"**Amount:** {memo.get('denial_amount')}",
            f"**CARC:** {memo.get('carc_code')}",
            f"**Policy Basis:** {memo.get('policy_basis')}",
            f"**Recommendation:** {memo.get('recommendation')}",
            f"**Reviewed by:** {memo.get('reviewed_by')}",
        ]

    return "\n".join(lines)


def _format_denial_only(denial: dict) -> str:
    rc = denial.get("root_cause", {})
    evidence = ", ".join(denial.get("evidence_required", []))
    return (
        "## DenialGPT — Denial Analysis\n\n"
        f"**Denial Type:** {denial.get('denial_type')}\n"
        f"**CARC Code:** {denial.get('carc_code')}\n"
        f"**Payer Reason:** {denial.get('payer_stated_reason')}\n"
        f"**Evidence Required:** {evidence}\n"
        f"**Appeal Deadline:** {denial.get('appeal_deadline')}\n\n"
        f"**Root Cause:** {rc.get('category')} — {rc.get('explanation', '')}\n"
        f"**Prevention:** {rc.get('prevention', '')}\n\n"
        "_No FHIR patient context provided. Select a patient in Prompt Opinion "
        "to run the full gap analysis and get an appeal viability verdict._"
    )


# ---------------------------------------------------------------------------
# A2A response builder
# ---------------------------------------------------------------------------

def _a2a_response(rpc_id: Any, result_text: str) -> JSONResponse:
    """Build a PO-compatible A2A v1 JSON-RPC response."""
    task_id = str(uuid.uuid4())
    return JSONResponse(
        content={
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "task": {
                    "id": task_id,
                    "contextId": task_id,
                    "status": {"state": "TASK_STATE_COMPLETED"},
                    "artifacts": [
                        {
                            "artifactId": str(uuid.uuid4()),
                            "parts": [{"text": result_text}],
                        }
                    ],
                }
            },
        },
        media_type="application/a2a+json",
    )


# ---------------------------------------------------------------------------
# FastMCP server (kept for dev/testing)
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "DenialGPT",
    instructions=(
        "Denial Prevention & Gap Analysis Agent for hospital billing "
        "and revenue cycle teams."
    ),
)


@mcp.tool()
async def analyze_denial(denial_text: str, payer: str) -> dict:
    """Analyze a denial letter. Returns type, CARC code, root cause, and prevention."""
    return await run_analyze_denial(denial_text=denial_text, payer=payer)


@mcp.tool()
async def fetch_clinical_evidence(
    denial_type: str,
    patient_id: str,
    date_of_service: str,
    fhir_base_url: str | None = None,
    access_token: str | None = None,
) -> dict:
    """Fetch FHIR clinical evidence for a denial type."""
    from tools.fetch_evidence import run_fetch_clinical_evidence
    return await run_fetch_clinical_evidence(
        denial_type=denial_type,
        patient_id=patient_id,
        date_of_service=date_of_service,
        fhir_base_url=fhir_base_url,
        access_token=access_token,
    )


@mcp.tool()
async def gap_analysis(denial_analysis: dict, clinical_evidence: dict) -> dict:
    """Compare payer requirements vs. clinical evidence. Returns appeal viability verdict."""
    from tools.gap_analysis import run_gap_analysis
    return await run_gap_analysis(
        denial_analysis=denial_analysis,
        clinical_evidence=clinical_evidence,
    )


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="DenialGPT", version="1.0.0")


# ── A2A v1 agent endpoint ──────────────────────────────────────────────────

@app.post("/")
async def a2a_agent(request: Request):
    """
    Primary A2A v1 endpoint. Prompt Opinion POSTs JSON-RPC 2.0 here.

    Handles:
      message/send          (A2A spec)
      SendMessage           (PO legacy alias)
      SendStreamingMessage  (PO legacy alias — returns non-streaming response)
    """
    # Auth
    if not _check_api_key(request):
        logger.warning("a2a_auth_rejected path=/ key=%s",
                       request.headers.get("X-API-Key", "")[:6])
        return JSONResponse(
            status_code=403,
            content={"error": "Forbidden", "detail": "Invalid or missing X-API-Key"},
        )

    # Parse JSON-RPC body
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32700, "message": "Parse error"}},
        )

    rpc_id = body.get("id")
    method = _METHOD_ALIASES.get(body.get("method", ""), body.get("method", ""))
    params  = body.get("params", {}) or {}
    message = params.get("message", {}) or {}

    logger.info("a2a_request id=%s method=%s", rpc_id, method)

    # Normalize roles in the message object
    role = _ROLE_ALIASES.get(message.get("role", ""), message.get("role", "user"))

    # Extract text parts
    parts = message.get("parts", []) or []
    text_parts = [p.get("text", "") for p in parts if "text" in p]
    user_text = "\n".join(text_parts).strip()

    # Extract FHIR context — check message.metadata first, then params.metadata
    metadata: dict = {}
    if isinstance(message.get("metadata"), dict):
        metadata = message["metadata"]
    elif isinstance(params.get("metadata"), dict):
        metadata = params["metadata"]

    fhir_ctx = _extract_fhir_context(metadata)
    if fhir_ctx:
        logger.info("fhir_context_found patient_id=%s", fhir_ctx.get("patientId"))
    else:
        logger.info("fhir_context_not_found")

    # Handle unsupported methods
    if method not in ("message/send", "message/stream"):
        return JSONResponse(
            content={
                "jsonrpc": "2.0", "id": rpc_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            },
            media_type="application/a2a+json",
        )

    if not user_text:
        return _a2a_response(
            rpc_id,
            "Please paste a denial letter or describe the claim denial to analyze.",
        )

    # ── Run tool chain ─────────────────────────────────────────────────────
    try:
        denial_result = await run_analyze_denial(denial_text=user_text, payer="Aetna")
        logger.info("analyze_denial done denial_type=%s carc=%s",
                    denial_result.get("denial_type"), denial_result.get("carc_code"))

        if fhir_ctx and fhir_ctx.get("patientId"):
            from tools.fetch_evidence import run_fetch_clinical_evidence
            from tools.gap_analysis import run_gap_analysis

            dos = date.today().isoformat()
            evidence_result = await run_fetch_clinical_evidence(
                denial_type=denial_result["denial_type"],
                patient_id=fhir_ctx["patientId"],
                date_of_service=dos,
                fhir_base_url=fhir_ctx.get("fhirUrl"),
                access_token=fhir_ctx.get("fhirToken"),
            )
            logger.info("fetch_evidence done resources=%s", evidence_result.get("resources_fetched"))

            gap_result = await run_gap_analysis(
                denial_analysis=denial_result,
                clinical_evidence=evidence_result,
            )
            logger.info("gap_analysis done viability=%s", gap_result.get("appeal_viability"))

            result_text = _format_full_result(denial_result, gap_result)
        else:
            result_text = _format_denial_only(denial_result)

    except Exception as exc:
        logger.exception("a2a_tool_chain_error")
        result_text = f"Error processing denial: {exc}"

    return _a2a_response(rpc_id, result_text)


# ── Agent card endpoints ───────────────────────────────────────────────────

@app.get("/.well-known/agent-card.json")
async def agent_card_v1():
    """A2A v1 agent card — public, no auth required."""
    return JSONResponse(content=_build_agent_card(), media_type="application/json")


@app.get("/.well-known/agent.json")
async def agent_card_legacy():
    """Legacy agent card path — kept for backward compat."""
    return JSONResponse(content=_build_agent_card(), media_type="application/json")


# ── Health check ───────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    base_url = os.getenv("AGENT_BASE_URL", "https://denialgpt.onrender.com")
    dev_mode = not all([
        os.getenv("FHIR_BASE_URL", "").startswith("https://"),
        os.getenv("DEV_ACCESS_TOKEN", "dev-token") != "dev-token",
    ])
    return {
        "status": "ok",
        "service": "DenialGPT",
        "version": "1.0.0",
        "tools_registered": ["analyze_denial", "fetch_clinical_evidence", "gap_analysis"],
        "agent_card": f"{base_url}/.well-known/agent-card.json",
        "dev_mode": dev_mode,
    }


# ── FastMCP mount (dev/testing) ────────────────────────────────────────────

app.mount("/mcp", mcp.http_app())


# ---------------------------------------------------------------------------
# Standalone run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
