"""
check_claim_policy — Google ADK Tool (Person B)

Takes a claim draft (CPT + ICD-10 + payer + place of service), retrieves
relevant CMS LCD policy chunks from ChromaDB via RAG, asks Claude to identify
denial risks, and returns structured risk flags plus a payer_intelligence block
from PAYER_PATTERNS.

ADK integration
---------------
The public entry point is the ``check_claim_policy`` async function at the
bottom of this module.  Its signature follows the Google ADK tool convention:
all claim inputs as typed parameters, followed by ``tool_context: ToolContext``
as the final argument.  Person A imports and registers it like so:

    from prevention.check_claim_policy import check_claim_policy

    root_agent = Agent(..., tools=[..., check_claim_policy])

The internal ``run_check_claim_policy(claim: ClaimDraft)`` function does the
heavy lifting and is kept separate so it can also be called from tests and the
standalone smoke test below.

Inputs (check_claim_policy parameters):
    cpt_code              — e.g. "73721"
    icd10_code            — e.g. "M17.11"
    payer                 — e.g. "Aetna"
    place_of_service      — e.g. "outpatient"
    procedure_description — e.g. "MRI knee without contrast"

Outputs (dict):
    overall_risk        — "LOW" | "MEDIUM" | "HIGH" | "UNKNOWN"
    risk_flags          — list of flag dicts (flag, severity, policy_basis, recommendation)
    policy_references   — LCD source names cited
    recommended_fixes   — plain-English actions before submitting
    payer_intelligence  — from PAYER_PATTERNS, None if no entry exists

Environment:
    ANTHROPIC_API_KEY   — required for Claude call
    VOYAGE_API_KEY      — required for ChromaDB retrieval

Usage (standalone smoke test):
    python -m prevention.check_claim_policy
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from google.adk.tools import ToolContext
from pydantic import BaseModel

from policy_kb.retrieve import retrieve_policy_chunks
from shared.payer_patterns import get_payer_pattern

# ---------------------------------------------------------------------------
# Bootstrap env — safe to call multiple times
# ---------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CLAUDE_MODEL: str = "claude-sonnet-4-5"
TOP_K_CHUNKS: int = 5


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ClaimDraft(BaseModel):
    """Input schema for the check_claim_policy MCP tool."""

    cpt_code: str
    """CPT procedure code, e.g. "73721" (knee MRI)."""

    icd10_code: str
    """ICD-10 diagnosis code, e.g. "M17.11" (primary osteoarthritis, right knee)."""

    payer: str
    """Insurance payer name, e.g. "Aetna"."""

    place_of_service: str
    """Service setting, e.g. "outpatient" or "inpatient"."""

    procedure_description: str
    """Human-readable procedure description, e.g. "MRI knee without contrast"."""


class PolicyCheckResult(BaseModel):
    """Output schema for the check_claim_policy MCP tool."""

    overall_risk: str
    """Aggregate denial risk level: "LOW", "MEDIUM", "HIGH", or "UNKNOWN"."""

    risk_flags: list[dict]
    """
    List of identified risk flags. Each dict has:
        flag            — short description of the risk
        severity        — "LOW", "MEDIUM", or "HIGH"
        policy_basis    — specific LCD/NCD section this flag comes from
        recommendation  — what to do to resolve this flag
    """

    policy_references: list[str]
    """Names of LCD/NCD sources cited in the analysis."""

    recommended_fixes: list[str]
    """Ordered list of plain-English actions to take before submitting."""

    payer_intelligence: dict | None
    """
    Payer pattern intelligence from PAYER_PATTERNS.
    Contains denial_rate, top_reason, appeal_win_rate, winning_evidence, prevention.
    None if no entry exists for this payer/CPT/ICD-10 combination.
    """


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

SYSTEM_PROMPT: str = (
    "You are a medical billing compliance expert. You analyze insurance claims "
    "against CMS coverage policies to identify denial risks before submission. "
    "You are thorough, precise, and always cite the specific policy section your "
    "flags come from. Respond only in valid JSON."
)

_USER_PROMPT_TEMPLATE: str = """Analyze this claim for denial risk:
- CPT Code: {cpt_code}
- Diagnosis: {icd10_code}
- Payer: {payer}
- Place of Service: {place_of_service}
- Procedure: {procedure_description}

Relevant coverage policy excerpts:
{policy_chunks}
{payer_intel_section}
Return a JSON object with exactly these fields:
- overall_risk: one of LOW / MEDIUM / HIGH / UNKNOWN
- risk_flags: array of objects, each with:
    - flag: short description of the specific risk (string)
    - severity: HIGH, MEDIUM, or LOW (string)
    - policy_basis: exact LCD name and section this flag comes from (string)
    - recommendation: specific action to resolve this flag (string)
- policy_references: array of LCD/NCD source document names cited (strings)
- recommended_fixes: array of plain-English fix instructions ordered by priority (strings)

Rules:
- Flag risks supported by EITHER the policy excerpts OR the payer intelligence data above.
- Payer intelligence reflects real historical denial patterns — treat it as authoritative.
- If prior authorization is listed as a top denial reason in payer intelligence, always flag it as HIGH severity.
- If conservative therapy is listed in winning_evidence, flag documentation gaps as MEDIUM severity.
- If conservative therapy requirements are mentioned in the policy excerpts, flag them explicitly.
- Set overall_risk to HIGH if any HIGH severity flag exists, MEDIUM if any MEDIUM flag exists, LOW otherwise.
- Do not include any text outside the JSON object."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _format_policy_chunks(chunks: list[dict]) -> str:
    """Format retrieved ChromaDB chunks into a numbered list for the LLM prompt.

    Parameters
    ----------
    chunks : list[dict]
        Each dict has ``text``, ``source``, and ``score`` keys.

    Returns
    -------
    str
        Formatted string ready to embed in the user prompt.
    """
    if not chunks:
        return "(no policy excerpts available)"

    parts: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        source = chunk.get("source", "unknown")
        text = chunk.get("text", "").strip()
        parts.append(f"[{i}] Source: {source}\n{text}")

    return "\n\n".join(parts)


def _build_payer_intel_section(payer_intel: dict | None) -> str:
    """Format payer intelligence as a prompt section Claude can reason from.

    When PAYER_PATTERNS has an entry for this combination, this section gives
    Claude concrete historical denial data — denial rate, top reason, winning
    evidence — so it can generate accurate flags even when the Policy KB chunks
    don't perfectly match the procedure.

    Returns an empty string when no payer intel exists.
    """
    if not payer_intel:
        return ""

    lines = [
        "\nPayer intelligence for this exact combination "
        f"({payer_intel.get('top_reason', 'see below')}):",
        f"- Historical denial rate: {payer_intel.get('denial_rate', 'unknown')}",
        f"- Top denial reason: {payer_intel.get('top_reason', 'unknown')}",
        f"- Appeal win rate: {payer_intel.get('appeal_win_rate', 'unknown')}",
        f"- Evidence that wins appeals: {payer_intel.get('winning_evidence', 'unknown')}",
        f"- Prevention note: {payer_intel.get('prevention', 'unknown')}",
        "",
    ]
    return "\n".join(lines)


def _build_user_prompt(
    claim: ClaimDraft,
    formatted_chunks: str,
    payer_intel: dict | None = None,
) -> str:
    """Render the user prompt template with claim details, policy chunks, and payer intel."""
    return _USER_PROMPT_TEMPLATE.format(
        cpt_code=claim.cpt_code,
        icd10_code=claim.icd10_code,
        payer=claim.payer,
        place_of_service=claim.place_of_service,
        procedure_description=claim.procedure_description,
        policy_chunks=formatted_chunks,
        payer_intel_section=_build_payer_intel_section(payer_intel),
    )


async def _call_claude(user_prompt: str) -> str:
    """Send the prompt to Claude and return the raw response text.

    Uses the async Anthropic client so the FastMCP event loop is not blocked.

    Parameters
    ----------
    user_prompt : str
        The rendered user prompt including claim details and policy excerpts.

    Returns
    -------
    str
        Raw text content of Claude's first message block.

    Raises
    ------
    EnvironmentError
        If ANTHROPIC_API_KEY is not set.
    anthropic.APIError
        On any API-level failure (propagated to caller for handling).
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. Add it to .env at project root."
        )

    client = anthropic.AsyncAnthropic(api_key=api_key)
    message = await client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return message.content[0].text


def _parse_claude_response(raw: str) -> dict:
    """Parse Claude's JSON response into a plain dict.

    Handles the common case where Claude wraps JSON in a markdown code block
    (```json ... ```). Falls back to a safe UNKNOWN result on parse failure.

    Parameters
    ----------
    raw : str
        Raw text returned by Claude.

    Returns
    -------
    dict
        Parsed JSON dict, or a safe fallback dict if parsing fails.
    """
    # Strip markdown code fences if present
    cleaned = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    try:
        parsed = json.loads(cleaned)
        # Validate required top-level keys are present
        for key in ("overall_risk", "risk_flags", "policy_references", "recommended_fixes"):
            if key not in parsed:
                parsed[key] = [] if key != "overall_risk" else "UNKNOWN"
        # Normalise overall_risk to uppercase
        parsed["overall_risk"] = str(parsed["overall_risk"]).upper()
        if parsed["overall_risk"] not in {"LOW", "MEDIUM", "HIGH", "UNKNOWN"}:
            parsed["overall_risk"] = "UNKNOWN"
        return parsed

    except (json.JSONDecodeError, ValueError):
        import sys
        print(
            f"\n[_parse_claude_response] JSON parse failed.\n"
            f"Raw response was ({len(raw)} chars):\n{raw[:800]}\n",
            file=sys.stderr,
        )
        return {
            "overall_risk": "UNKNOWN",
            "risk_flags": [
                {
                    "flag": "Policy analysis could not be completed",
                    "severity": "HIGH",
                    "policy_basis": "Internal error — Claude response was not valid JSON",
                    "recommendation": "Retry the check or perform manual policy review",
                }
            ],
            "policy_references": [],
            "recommended_fixes": [
                "Manual policy review required — automated analysis failed. "
                "Contact billing compliance before submitting this claim."
            ],
        }


# ---------------------------------------------------------------------------
# Public tool function
# ---------------------------------------------------------------------------

async def run_check_claim_policy(claim: ClaimDraft) -> PolicyCheckResult:
    """Run the full check_claim_policy pipeline for a claim draft.

    Steps
    -----
    1. Retrieve relevant policy chunks from ChromaDB (RAG).
    2. Fetch payer intelligence from PAYER_PATTERNS.
    3. If no chunks returned, return early with UNKNOWN risk.
    4. Build and send prompt to Claude.
    5. Parse Claude's JSON response.
    6. Attach payer_intelligence block to the result.

    Parameters
    ----------
    claim : ClaimDraft
        The claim draft to analyse.

    Returns
    -------
    PolicyCheckResult
        Structured risk assessment with flags, references, fixes, and
        payer pattern intelligence.
    """
    # ------------------------------------------------------------------
    # 1. Retrieve policy chunks
    # ------------------------------------------------------------------
    try:
        chunks = await retrieve_policy_chunks(
            cpt_code=claim.cpt_code,
            icd10_code=claim.icd10_code,
            payer=claim.payer,
            procedure_description=claim.procedure_description,
            top_k=TOP_K_CHUNKS,
        )
    except Exception as exc:
        chunks = []
        _warn(f"ChromaDB retrieval failed: {exc}")

    # ------------------------------------------------------------------
    # 2. Payer intelligence (always attempt — independent of RAG)
    # ------------------------------------------------------------------
    payer_intel = get_payer_pattern(
        payer=claim.payer,
        cpt_code=claim.cpt_code,
        icd10_code=claim.icd10_code,
    )

    # ------------------------------------------------------------------
    # 3. Early return if no policy data available
    # ------------------------------------------------------------------
    if not chunks:
        return PolicyCheckResult(
            overall_risk="UNKNOWN",
            risk_flags=[
                {
                    "flag": "No policy data found for this CPT/ICD-10 combination",
                    "severity": "HIGH",
                    "policy_basis": "Policy Knowledge Base returned no results",
                    "recommendation": (
                        "Perform manual policy review against payer guidelines "
                        "before submitting this claim."
                    ),
                }
            ],
            policy_references=[],
            recommended_fixes=[
                "No CMS LCD/NCD data found for this claim combination. "
                "Manual compliance review required before submission."
            ],
            payer_intelligence=payer_intel,
        )

    # ------------------------------------------------------------------
    # 4. Build prompt — include payer intel so Claude has denial pattern data
    #    even when the Policy KB chunks don't perfectly match the procedure
    # ------------------------------------------------------------------
    formatted_chunks = _format_policy_chunks(chunks)
    user_prompt = _build_user_prompt(claim, formatted_chunks, payer_intel=payer_intel)

    # ------------------------------------------------------------------
    # 5. Call Claude
    # ------------------------------------------------------------------
    try:
        raw_response = await _call_claude(user_prompt)
    except Exception as exc:
        _warn(f"Claude API call failed: {exc}")
        return PolicyCheckResult(
            overall_risk="UNKNOWN",
            risk_flags=[
                {
                    "flag": "Policy analysis unavailable — LLM call failed",
                    "severity": "HIGH",
                    "policy_basis": f"Internal error: {exc}",
                    "recommendation": "Retry or perform manual review.",
                }
            ],
            policy_references=[],
            recommended_fixes=["Automated analysis failed. Manual review required."],
            payer_intelligence=payer_intel,
        )

    # ------------------------------------------------------------------
    # 6. Parse response
    # ------------------------------------------------------------------
    parsed = _parse_claude_response(raw_response)

    # ------------------------------------------------------------------
    # 7. Build and return result with payer_intelligence attached
    # ------------------------------------------------------------------
    return PolicyCheckResult(
        overall_risk=parsed["overall_risk"],
        risk_flags=parsed.get("risk_flags", []),
        policy_references=parsed.get("policy_references", []),
        recommended_fixes=parsed.get("recommended_fixes", []),
        payer_intelligence=payer_intel,
    )


def _warn(msg: str) -> None:
    """Print a warning to stderr without raising."""
    import sys
    print(f"[check_claim_policy WARNING] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Google ADK tool entry point
# ---------------------------------------------------------------------------

async def check_claim_policy(
    cpt_code: str,
    icd10_code: str,
    payer: str,
    place_of_service: str,
    procedure_description: str,
    tool_context: ToolContext,
) -> dict:
    """
    Check a claim draft against CMS coverage policies and payer denial patterns
    to identify denial risks before submission.

    Retrieves relevant LCD/NCD policy excerpts from the Policy Knowledge Base
    (ChromaDB RAG), combines them with historical denial data from PAYER_PATTERNS,
    and asks Claude to produce a structured risk assessment.

    Args:
        cpt_code:             CPT procedure code, e.g. "73721" (knee MRI without contrast).
        icd10_code:           ICD-10 diagnosis code, e.g. "M17.11" (primary osteoarthritis,
                              right knee).
        payer:                Insurance payer name, e.g. "Aetna".
        place_of_service:     Service setting, e.g. "outpatient" or "inpatient".
        procedure_description: Human-readable description, e.g. "MRI of the right knee
                              without contrast".
        tool_context:         Injected by the ADK runtime — not supplied by the caller.

    Returns:
        dict with keys:
            overall_risk (str)        — "LOW", "MEDIUM", "HIGH", or "UNKNOWN"
            risk_flags (list[dict])   — each has flag, severity, policy_basis, recommendation
            policy_references (list)  — LCD/NCD source names cited
            recommended_fixes (list)  — ordered plain-English actions before submitting
            payer_intelligence (dict) — historical denial data from PAYER_PATTERNS,
                                        or None if no entry exists for this combination
    """
    claim = ClaimDraft(
        cpt_code=cpt_code,
        icd10_code=icd10_code,
        payer=payer,
        place_of_service=place_of_service,
        procedure_description=procedure_description,
    )
    result = await run_check_claim_policy(claim)
    return result.model_dump()


# ---------------------------------------------------------------------------
# __main__ — demo scenario smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":

    async def _smoke_test() -> None:
        print("=" * 65)
        print("CHECK_CLAIM_POLICY SMOKE TEST")
        print("=" * 65)

        claim = ClaimDraft(
            cpt_code="73721",
            icd10_code="M17.11",
            payer="Aetna",
            place_of_service="outpatient",
            procedure_description="MRI of the right knee without contrast",
        )

        print(f"\nClaim: CPT {claim.cpt_code} | {claim.icd10_code} | "
              f"{claim.payer} | {claim.place_of_service}")
        print(f"Procedure: {claim.procedure_description}\n")

        result = await run_check_claim_policy(claim)

        print(f"Overall Risk:  {result.overall_risk}")
        print(f"Risk Flags:    {len(result.risk_flags)}")
        print(f"Policy Refs:   {result.policy_references}")
        print(f"Payer Intel:   {'present' if result.payer_intelligence else 'None'}")
        print()

        if result.risk_flags:
            print("── Risk Flags ──────────────────────────────────────────")
            for flag in result.risk_flags:
                print(f"  [{flag.get('severity','?')}] {flag.get('flag','')}")
                print(f"       Basis:  {flag.get('policy_basis','')}")
                print(f"       Fix:    {flag.get('recommendation','')}")
                print()

        if result.recommended_fixes:
            print("── Recommended Fixes ───────────────────────────────────")
            for i, fix in enumerate(result.recommended_fixes, 1):
                print(f"  {i}. {fix}")
            print()

        if result.payer_intelligence:
            print("── Payer Intelligence ──────────────────────────────────")
            for k, v in result.payer_intelligence.items():
                print(f"  {k}: {v}")
            print()

        print("=" * 65)
        print("Full JSON output:")
        print(json.dumps(result.model_dump(), indent=2))

    asyncio.run(_smoke_test())
