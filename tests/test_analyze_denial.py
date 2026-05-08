"""
Tests for analyze_denial tool — Day 2.

Three hardcoded denial scenarios. Each test calls the real Claude API
(requires ANTHROPIC_API_KEY in .env) and asserts output structure + classification.

Usage:
    cd denialgpt
    pytest tests/test_analyze_denial.py -v -s
"""

from __future__ import annotations

import os

import pytest

from tools.analyze_denial import run_analyze_denial

# Skip all tests if no API key is configured
API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SKIP_REASON = "Set ANTHROPIC_API_KEY in .env to run analyze_denial integration tests"

# ---------------------------------------------------------------------------
# Fixtures: 3 hardcoded denial letters
# ---------------------------------------------------------------------------

DENIAL_MEDICAL_NECESSITY = """\
Claim denied. CARC Code 50: Medical necessity not established.
Patient: Michael Torres. Date of service: May 2, 2026.
Procedure: Knee MRI (CPT 71046). Diagnosis: M17.11.
Payer requires documentation of failed conservative therapy (6+ weeks PT or NSAID trial).
No such documentation was submitted with the claim.
Appeal deadline: June 15, 2026.
"""

DENIAL_PROCESS_FAILURE = """\
Claim denied. CARC Code 197: Precertification/prior authorization absent.
Patient: James Washington. Date of service: April 28, 2026.
Procedure: Knee MRI (CPT 71046). Payer: Aetna.
Prior authorization was required and not obtained before service.
Appeal deadline: June 10, 2026.
"""

DENIAL_CODING_ERROR = """\
Claim denied. CARC Code 4: Service inconsistent with modifier.
Patient: Sarah Chen. Date of service: May 1, 2026.
Procedure billed: CPT 71046 with modifier 26. Diagnosis: M17.11.
The modifier submitted is inconsistent with the place of service and procedure combination.
Appeal deadline: June 20, 2026.
"""


# ---------------------------------------------------------------------------
# Shared assertion helpers
# ---------------------------------------------------------------------------

def assert_valid_output_structure(result: dict) -> None:
    """Assert that the output matches the strict schema."""
    required_keys = {
        "denial_type", "carc_code", "payer_stated_reason",
        "evidence_required", "appeal_deadline", "root_cause",
    }
    assert required_keys.issubset(result.keys()), (
        f"Missing keys: {required_keys - result.keys()}"
    )

    assert result["denial_type"] in {
        "Medical Necessity", "Coding Error",
        "Missing Documentation", "Untimely Filing",
    }

    assert isinstance(result["carc_code"], str)
    assert isinstance(result["evidence_required"], list)
    assert len(result["evidence_required"]) >= 1

    rc = result["root_cause"]
    assert isinstance(rc, dict)
    assert rc["category"] in {
        "DOCUMENTATION_GAP", "CODING_ERROR",
        "PROCESS_FAILURE", "CLINICAL_CRITERIA_UNMET",
    }
    assert rc["explanation"] and len(rc["explanation"]) > 0
    assert rc["prevention"] and len(rc["prevention"]) > 0


# ---------------------------------------------------------------------------
# Test Case 1: Medical Necessity — Michael Torres (DOCUMENTATION_GAP)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY, reason=SKIP_REASON)
async def test_medical_necessity_denial():
    """
    Michael Torres scenario: payer denies knee MRI for lack of
    conservative therapy documentation. Docs exist but weren't submitted.
    """
    result = await run_analyze_denial(
        denial_text=DENIAL_MEDICAL_NECESSITY,
        payer="Aetna",
    )

    assert_valid_output_structure(result)
    assert result["denial_type"] == "Medical Necessity"
    assert result["carc_code"] == "50"
    assert result["root_cause"]["category"] == "DOCUMENTATION_GAP"
    assert result["root_cause"]["prevention"]  # non-empty


# ---------------------------------------------------------------------------
# Test Case 2: Process Failure — James Washington (PROCESS_FAILURE)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY, reason=SKIP_REASON)
async def test_process_failure_denial():
    """
    James Washington scenario: prior auth not obtained before service.
    This is a workflow miss, not a documentation or coding issue.
    """
    result = await run_analyze_denial(
        denial_text=DENIAL_PROCESS_FAILURE,
        payer="Aetna",
    )

    assert_valid_output_structure(result)
    assert result["denial_type"] == "Missing Documentation"
    assert result["root_cause"]["category"] == "PROCESS_FAILURE"
    assert result["root_cause"]["prevention"]  # non-empty


# ---------------------------------------------------------------------------
# Test Case 3: Coding Error — Sarah Chen (CODING_ERROR)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.skipif(not API_KEY, reason=SKIP_REASON)
async def test_coding_error_denial():
    """
    Sarah Chen scenario: modifier 26 inconsistent with place of service.
    Pure billing mistake.
    """
    result = await run_analyze_denial(
        denial_text=DENIAL_CODING_ERROR,
        payer="Aetna",
    )

    assert_valid_output_structure(result)
    assert result["denial_type"] == "Coding Error"
    assert result["carc_code"] == "4"
    assert result["root_cause"]["category"] == "CODING_ERROR"
    assert result["root_cause"]["prevention"]  # non-empty
