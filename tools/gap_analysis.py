"""
gap_analysis — MCP Tool (Day 3)

Compares payer requirements vs. clinical record.
Outputs: evidence_found, evidence_missing, appeal_viability, reasoning, next_steps.

Single Claude API call with chain-of-thought prompt.
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
PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "gap_analysis.txt"

VALID_VIABILITIES = {"STRONG", "WEAK", "DO NOT APPEAL"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _validate_output(result: dict[str, Any]) -> dict[str, Any]:
    """Validate and coerce the LLM output to match our strict schema."""
    required = ["evidence_found", "evidence_missing", "appeal_viability",
                "reasoning", "next_steps"]
    for field in required:
        if field not in result:
            raise ValueError(f"Missing required field: {field}")

    if result["appeal_viability"] not in VALID_VIABILITIES:
        raise ValueError(
            f"Invalid appeal_viability '{result['appeal_viability']}'. "
            f"Must be one of: {VALID_VIABILITIES}"
        )

    if not isinstance(result["evidence_found"], list):
        result["evidence_found"] = []
    if not isinstance(result["evidence_missing"], list):
        result["evidence_missing"] = []
    if not isinstance(result["next_steps"], list):
        result["next_steps"] = [str(result["next_steps"])]
    if not isinstance(result["reasoning"], str):
        result["reasoning"] = str(result["reasoning"])

    return result


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

async def run_gap_analysis(
    denial_analysis: dict[str, Any],
    clinical_evidence: dict[str, Any],
    api_key: str | None = None,
) -> dict[str, Any]:
    """
    Compare payer requirements vs. clinical evidence.

    Args:
        denial_analysis: Output from analyze_denial tool.
        clinical_evidence: Output from fetch_clinical_evidence tool.
        api_key: Anthropic API key (falls back to ANTHROPIC_API_KEY env).

    Returns:
        Structured gap analysis with appeal_viability verdict.
    """
    key = api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError(
            "No API key provided. Set ANTHROPIC_API_KEY in .env or pass api_key."
        )

    system_prompt = _load_system_prompt()

    user_message = (
        "## DENIAL ANALYSIS\n"
        f"```json\n{json.dumps(denial_analysis, indent=2)}\n```\n\n"
        "## CLINICAL EVIDENCE\n"
        f"```json\n{json.dumps(clinical_evidence, indent=2)}\n```\n\n"
        "Analyze the gap between what the payer requires and what exists in the "
        "clinical record. Return your assessment as a JSON object."
    )

    client = anthropic.AsyncAnthropic(api_key=key)

    response = await client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    raw_text = response.content[0].text.strip()

    # Strip markdown fences if present
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
