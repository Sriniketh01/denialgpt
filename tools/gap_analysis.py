"""
gap_analysis -- MCP Tool (Day 3)

Compares payer requirements vs. clinical record.
Outputs: evidence_found, evidence_missing, appeal_viability, reasoning,
         next_steps, payer_intelligence (from PAYER_PATTERNS), writeoff_memo.

Single Claude API call with chain-of-thought prompt.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("denialgpt.gap_analysis")

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


def _coerce_string_list(value: Any) -> list[str]:
    """Coerce LLM output to a flat list of strings."""
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            # LLM sometimes returns objects instead of strings -- flatten them
            parts = [str(v) for v in item.values() if str(v).strip()]
            if parts:
                result.append(" -- ".join(parts))
    return result


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

    result["evidence_found"]   = _coerce_string_list(result.get("evidence_found"))
    result["evidence_missing"] = _coerce_string_list(result.get("evidence_missing"))

    if not isinstance(result["next_steps"], list):
        result["next_steps"] = [str(result["next_steps"])]
    else:
        result["next_steps"] = [str(s) for s in result["next_steps"]]

    if not isinstance(result["reasoning"], str):
        result["reasoning"] = str(result["reasoning"])

    # writeoff_memo: only present and non-null on DO NOT APPEAL
    if result["appeal_viability"] != "DO NOT APPEAL":
        result["writeoff_memo"] = None
    elif not isinstance(result.get("writeoff_memo"), dict):
        result["writeoff_memo"] = None

    return result


def _inject_payer_intelligence(
    result: dict[str, Any],
    denial_analysis: dict[str, Any],
) -> dict[str, Any]:
    """
    Look up PAYER_PATTERNS for this (payer, CPT, ICD-10) and inject the
    intel block. Degrades gracefully if no match found or import fails.
    """
    try:
        from shared.payer_patterns import get_payer_pattern

        payer  = denial_analysis.get("payer", "Aetna")
        cpt    = "27447"   # default for demo (TKA)
        icd10  = "M17.11"  # default for demo
        carc   = denial_analysis.get("carc_code", "")

        # Try to extract CPT code from evidence_required list
        for item in denial_analysis.get("evidence_required", []):
            hit = re.search(r"\b(\d{5})\b", str(item))
            if hit:
                cpt = hit.group(1)
                break

        # 4-key lookup (post-denial with CARC) falls back to 3-key automatically
        pattern = get_payer_pattern(payer, cpt, icd10, carc_code=carc or None)
        if pattern:
            result["payer_intelligence"] = {
                "payer": payer,
                "cpt_code": cpt,
                "icd10_code": icd10,
                "denial_rate": pattern["denial_rate"],
                "top_reason": pattern["top_reason"],
                "appeal_win_rate": pattern["appeal_win_rate"],
                "winning_evidence": pattern["winning_evidence"],
                "prevention": pattern.get("prevention", ""),
            }
            logger.info(
                "payer_intelligence injected payer=%s cpt=%s win_rate=%s",
                payer, cpt, pattern["appeal_win_rate"],
            )
        else:
            result["payer_intelligence"] = None
            logger.info("payer_intelligence no match for %s/%s/%s", payer, cpt, icd10)
    except Exception:
        logger.warning("payer_intelligence lookup failed", exc_info=True)
        result["payer_intelligence"] = None

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
        denial_analysis:  Output from analyze_denial tool.
        clinical_evidence: Output from fetch_clinical_evidence tool.
        api_key: Anthropic API key (falls back to ANTHROPIC_API_KEY env).

    Returns:
        Structured gap analysis with appeal_viability verdict plus
        payer_intelligence and writeoff_memo (when applicable).
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
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        raw_text = "\n".join(lines).strip()

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"LLM returned invalid JSON. Parse error: {e}\n"
            f"Raw response:\n{raw_text}"
        ) from e

    validated = _validate_output(result)
    validated = _inject_payer_intelligence(validated, denial_analysis)

    # Stamp writeoff_memo with current UTC timestamp
    if validated.get("writeoff_memo") and isinstance(validated["writeoff_memo"], dict):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        validated["writeoff_memo"]["reviewed_by"] = f"DenialGPT | {ts}"

    return validated
