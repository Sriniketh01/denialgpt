"""
analyze_denial — MCP Tool (Day 2)

Reads a denial letter or FHIR ExplanationOfBenefit, classifies denial type,
extracts CARC codes, identifies payer objection, evidence required, appeal
deadline, and performs root-cause analysis.

Single Claude API call with structured extraction prompt.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL = "claude-sonnet-4-20250514"

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "analyze_denial.txt"

VALID_DENIAL_TYPES = {
    "Medical Necessity",
    "Coding Error",
    "Missing Documentation",
    "Untimely Filing",
}

VALID_ROOT_CAUSE_CATEGORIES = {
    "DOCUMENTATION_GAP",
    "CODING_ERROR",
    "PROCESS_FAILURE",
    "CLINICAL_CRITERIA_UNMET",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_system_prompt() -> str:
    """Load the system prompt from prompts/analyze_denial.txt."""
    return PROMPT_PATH.read_text(encoding="utf-8")


def _validate_output(result: dict[str, Any]) -> dict[str, Any]:
    """
    Validate and coerce the LLM output to match our strict schema.
    Raises ValueError if critical fields are missing or invalid.
    """
    required = ["denial_type", "carc_code", "payer_stated_reason",
                "evidence_required", "appeal_deadline", "root_cause"]
    for field in required:
        if field not in result:
            raise ValueError(f"Missing required field: {field}")

    if result["denial_type"] not in VALID_DENIAL_TYPES:
        raise ValueError(
            f"Invalid denial_type '{result['denial_type']}'. "
            f"Must be one of: {VALID_DENIAL_TYPES}"
        )

    result["carc_code"] = str(result["carc_code"])

    if not isinstance(result["evidence_required"], list):
        result["evidence_required"] = [str(result["evidence_required"])]

    rc = result.get("root_cause")
    if not isinstance(rc, dict):
        raise ValueError("root_cause must be a JSON object")

    for rc_field in ["category", "explanation", "prevention"]:
        if rc_field not in rc:
            raise ValueError(f"Missing root_cause.{rc_field}")

    if rc["category"] not in VALID_ROOT_CAUSE_CATEGORIES:
        raise ValueError(
            f"Invalid root_cause.category '{rc['category']}'. "
            f"Must be one of: {VALID_ROOT_CAUSE_CATEGORIES}"
        )

    if not rc["prevention"] or not rc["prevention"].strip():
        raise ValueError("root_cause.prevention must be non-empty")

    return result


# ---------------------------------------------------------------------------
# Core function (callable directly or via MCP)
# ---------------------------------------------------------------------------

async def run_analyze_denial(
    denial_text: str,
    payer: str,
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    Analyze a denial letter or FHIR ExplanationOfBenefit JSON.

    Args:
        denial_text: Raw denial letter text OR serialized FHIR EOB JSON.
        payer: Payer name (e.g., "Aetna").
        api_key: Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.

    Returns:
        Structured denial analysis dict matching the output schema.

    Raises:
        ValueError: If the LLM output fails validation.
        anthropic.APIError: If the Claude API call fails.
    """
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError(
            "No API key provided. Set ANTHROPIC_API_KEY in .env or pass api_key."
        )

    system_prompt = _load_system_prompt()

    user_message = (
        f"Payer: {payer}\n\n"
        f"Denial notification:\n{denial_text}"
    )

    client = anthropic.AsyncAnthropic(api_key=key)

    response = await client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    raw_text = response.content[0].text.strip()

    # Strip markdown fences if the model added them despite instructions
    if raw_text.startswith("```"):
        lines = raw_text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        raw_text = "\n".join(lines).strip()

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"LLM returned invalid JSON. Parse error: {e}\n"
            f"Raw response:\n{raw_text}"
        ) from e

    return _validate_output(result)
