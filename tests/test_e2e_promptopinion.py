"""
E2E Test Suite — Prompt Opinion FHIR Sandbox
=============================================

Runs against real APIs — NO mocks.

Prerequisites:
  1. Seed patients and export IDs:
       python -m patients.seed_patients --fhir-url <PO_URL> --token <PO_TOKEN> --verify
     This writes .env.promptopinion with TORRES_PATIENT_ID, CHEN_PATIENT_ID,
     WASHINGTON_PATIENT_ID, FHIR_BASE_URL, DEV_ACCESS_TOKEN.

  2. Load .env.promptopinion before running:
       set -a && source .env.promptopinion && set +a  # Linux/macOS
       # or use pytest-dotenv / invoke directly with env pre-loaded

  3. Run:
       pytest tests/test_e2e_promptopinion.py -v --timeout=60

All tests are skipped if TORRES_PATIENT_ID is not set in the environment.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Load .env.promptopinion if it exists and TORRES_PATIENT_ID isn't already set
# ---------------------------------------------------------------------------

_PO_ENV = Path(__file__).resolve().parent.parent / ".env.promptopinion"
if _PO_ENV.exists() and not os.getenv("TORRES_PATIENT_ID"):
    from dotenv import load_dotenv
    load_dotenv(_PO_ENV)


# ---------------------------------------------------------------------------
# Shared test fixtures / constants
# ---------------------------------------------------------------------------

TORRES_DENIAL = (
    "Claim denied. CARC Code 50: Medical necessity not established.\n"
    "Patient: Michael Torres. Date of service: May 2, 2026.\n"
    "Procedure: Knee MRI (CPT 71046). Diagnosis: M17.11.\n"
    "Payer requires documentation of failed conservative therapy "
    "(6+ weeks PT or NSAID trial).\n"
    "No such documentation was submitted with the claim.\n"
    "Appeal deadline: June 15, 2026."
)

# Chen uses the same procedure/payer/reason — different patient record
CHEN_DENIAL = TORRES_DENIAL.replace("Michael Torres", "Sarah Chen")

DATE_OF_SERVICE = "2026-05-02"
PAYER = "Aetna"


def _env(key: str) -> str | None:
    return os.environ.get(key) or None


# Derived at import time so skipif can reference them
TORRES_ID = _env("TORRES_PATIENT_ID")
CHEN_ID = _env("CHEN_PATIENT_ID")
FHIR_URL = _env("FHIR_BASE_URL")
ACCESS_TOKEN = _env("DEV_ACCESS_TOKEN")
RAILWAY_URL = (_env("RAILWAY_URL") or "").rstrip("/")

_SKIP_ALL = pytest.mark.skipif(
    not TORRES_ID,
    reason=(
        "TORRES_PATIENT_ID not set. "
        "Run: python -m patients.seed_patients ... then load .env.promptopinion"
    ),
)
_SKIP_RAILWAY = pytest.mark.skipif(
    not RAILWAY_URL,
    reason="RAILWAY_URL not set — skipping post-deploy reachability test",
)


# ---------------------------------------------------------------------------
# Import tools at module level so import errors surface immediately
# (rather than inside a test, where they'd count as an error not a skip)
# ---------------------------------------------------------------------------

from tools.analyze_denial import run_analyze_denial  # noqa: E402
from tools.fetch_evidence import run_fetch_clinical_evidence  # noqa: E402
from tools.gap_analysis import run_gap_analysis  # noqa: E402


# ===========================================================================
# Test 1 — analyze_denial (pure LLM, no FHIR)
# ===========================================================================

@_SKIP_ALL
@pytest.mark.asyncio
async def test_analyze_denial_torres():
    """
    analyze_denial classifies Torres denial correctly.
    Pure LLM call — no FHIR dependency.
    """
    t0 = time.monotonic()
    result = await run_analyze_denial(denial_text=TORRES_DENIAL, payer=PAYER)
    elapsed = time.monotonic() - t0

    assert isinstance(result, dict), "Result must be a dict"
    assert result["denial_type"] == "Medical Necessity", (
        f"Expected denial_type='Medical Necessity', got '{result['denial_type']}'"
    )
    assert result["carc_code"] == "50", (
        f"Expected carc_code='50', got '{result['carc_code']}'"
    )

    rc = result.get("root_cause", {})
    assert rc.get("category") == "DOCUMENTATION_GAP", (
        f"Expected root_cause.category='DOCUMENTATION_GAP', got '{rc.get('category')}'"
    )
    assert rc.get("prevention") and rc["prevention"].strip(), (
        "root_cause.prevention must be non-empty"
    )

    assert elapsed < 10, f"analyze_denial took {elapsed:.1f}s — must be < 10s"
    print(f"\n  ✓ analyze_denial completed in {elapsed:.2f}s")
    print(f"  denial_type: {result['denial_type']}")
    print(f"  carc_code:   {result['carc_code']}")
    print(f"  root_cause:  {rc['category']}")


# ===========================================================================
# Test 2 — fetch_clinical_evidence (live FHIR)
# ===========================================================================

@_SKIP_ALL
@pytest.mark.asyncio
async def test_fetch_clinical_evidence_torres():
    """
    fetch_clinical_evidence hits the real Prompt Opinion FHIR sandbox.
    Verifies Torres' medical necessity resources are present.
    """
    assert TORRES_ID, "TORRES_PATIENT_ID not set"
    assert FHIR_URL, "FHIR_BASE_URL not set"

    t0 = time.monotonic()
    result = await run_fetch_clinical_evidence(
        denial_type="Medical Necessity",
        patient_id=TORRES_ID,
        date_of_service=DATE_OF_SERVICE,
        fhir_base_url=FHIR_URL,
        access_token=ACCESS_TOKEN,
    )
    elapsed = time.monotonic() - t0

    assert isinstance(result, dict), "Result must be a dict"
    assert "Condition" in result["resources_fetched"], (
        f"Expected 'Condition' in resources_fetched, got: {result['resources_fetched']}"
    )
    assert result["fetch_errors"] == [], (
        f"fetch_errors must be empty, got: {result['fetch_errors']}"
    )
    assert len(result["fhir_resource_ids"]) > 0, (
        "fhir_resource_ids must contain at least 1 entry"
    )
    assert len(result["fhir_resource_ids"]) >= 1, "At least 1 resource must be returned"

    # Verify evidence dict has at least Condition key with data
    evidence = result.get("evidence", {})
    assert evidence.get("Condition"), "Condition evidence must be non-empty for Torres"

    assert elapsed < 15, f"fetch_clinical_evidence took {elapsed:.1f}s — must be < 15s"
    print(f"\n  ✓ fetch_clinical_evidence completed in {elapsed:.2f}s")
    print(f"  resources_fetched: {result['resources_fetched']}")
    print(f"  total resource IDs: {len(result['fhir_resource_ids'])}")


# ===========================================================================
# Test 3 — Full chain: Torres → STRONG appeal
# ===========================================================================

@_SKIP_ALL
@pytest.mark.asyncio
async def test_full_chain_torres_strong():
    """
    Full sequential chain: analyze_denial → fetch_clinical_evidence → gap_analysis.
    Torres has strong documentation (Ibuprofen x2, PT sessions, notes).
    Expected verdict: STRONG.
    """
    assert TORRES_ID, "TORRES_PATIENT_ID not set"
    assert FHIR_URL, "FHIR_BASE_URL not set"

    t_chain_start = time.monotonic()

    # Step 1 — analyze_denial
    t0 = time.monotonic()
    denial_result = await run_analyze_denial(denial_text=TORRES_DENIAL, payer=PAYER)
    t1 = time.monotonic()
    assert isinstance(denial_result, dict), "denial_result must be a dict"
    print(f"\n  Step 1 (analyze_denial): {t1 - t0:.2f}s")

    # Step 2 — fetch_clinical_evidence
    t0 = time.monotonic()
    evidence_result = await run_fetch_clinical_evidence(
        denial_type=denial_result["denial_type"],
        patient_id=TORRES_ID,
        date_of_service=DATE_OF_SERVICE,
        fhir_base_url=FHIR_URL,
        access_token=ACCESS_TOKEN,
    )
    t1 = time.monotonic()
    assert isinstance(evidence_result, dict), "evidence_result must be a dict"
    print(f"  Step 2 (fetch_evidence):  {t1 - t0:.2f}s")

    # Step 3 — gap_analysis
    t0 = time.monotonic()
    gap_result = await run_gap_analysis(
        denial_analysis=denial_result,
        clinical_evidence=evidence_result,
    )
    t1 = time.monotonic()
    assert isinstance(gap_result, dict), "gap_result must be a dict"
    print(f"  Step 3 (gap_analysis):    {t1 - t0:.2f}s")

    total_elapsed = time.monotonic() - t_chain_start
    assert total_elapsed < 45, f"Full chain took {total_elapsed:.1f}s — must be < 45s"

    # --- Assertions on gap_result ---
    assert gap_result["appeal_viability"] == "STRONG", (
        f"Expected appeal_viability='STRONG' for Torres, got '{gap_result['appeal_viability']}'\n"
        f"Reasoning: {gap_result.get('reasoning', '')}"
    )
    assert gap_result["reasoning"] and gap_result["reasoning"].strip(), (
        "reasoning must be non-empty"
    )
    assert gap_result["evidence_found"] and len(gap_result["evidence_found"]) > 0, (
        "evidence_found must contain at least 1 item for Torres"
    )

    print(f"\n  ✓ Full chain completed in {total_elapsed:.2f}s")
    print(f"  appeal_viability: {gap_result['appeal_viability']}")
    print(f"  evidence_found:   {gap_result['evidence_found'][:2]}...")
    print(f"  reasoning:        {gap_result['reasoning'][:120]}...")


# ===========================================================================
# Test 4 — Full chain: Chen → DO NOT APPEAL
# ===========================================================================

@_SKIP_ALL
@pytest.mark.asyncio
async def test_full_chain_chen_do_not_appeal():
    """
    Full sequential chain for Sarah Chen.
    Chen has minimal documentation (single Ibuprofen Rx, no PT, no notes).
    Expected verdict: DO NOT APPEAL (with write-off memo triggerable).
    """
    assert CHEN_ID, "CHEN_PATIENT_ID not set — run seed_patients"
    assert FHIR_URL, "FHIR_BASE_URL not set"

    t_chain_start = time.monotonic()

    # Step 1
    denial_result = await run_analyze_denial(denial_text=CHEN_DENIAL, payer=PAYER)
    assert isinstance(denial_result, dict)

    # Step 2
    evidence_result = await run_fetch_clinical_evidence(
        denial_type=denial_result["denial_type"],
        patient_id=CHEN_ID,
        date_of_service=DATE_OF_SERVICE,
        fhir_base_url=FHIR_URL,
        access_token=ACCESS_TOKEN,
    )
    assert isinstance(evidence_result, dict)

    # Step 3
    gap_result = await run_gap_analysis(
        denial_analysis=denial_result,
        clinical_evidence=evidence_result,
    )
    assert isinstance(gap_result, dict)

    total_elapsed = time.monotonic() - t_chain_start
    assert total_elapsed < 45, f"Full chain took {total_elapsed:.1f}s — must be < 45s"

    # --- Assertions ---
    assert gap_result["appeal_viability"] == "DO NOT APPEAL", (
        f"Expected appeal_viability='DO NOT APPEAL' for Chen, "
        f"got '{gap_result['appeal_viability']}'\n"
        f"Reasoning: {gap_result.get('reasoning', '')}"
    )
    assert gap_result["evidence_missing"] and len(gap_result["evidence_missing"]) > 0, (
        "evidence_missing must be non-empty when appeal_viability is DO NOT APPEAL"
    )
    assert gap_result["next_steps"] and len(gap_result["next_steps"]) > 0, (
        "next_steps must be non-empty even on DO NOT APPEAL (write-off guidance)"
    )

    print(f"\n  ✓ Chen chain completed in {total_elapsed:.2f}s")
    print(f"  appeal_viability: {gap_result['appeal_viability']}")
    print(f"  evidence_missing: {gap_result['evidence_missing'][:2]}...")
    print(f"  next_steps:       {gap_result['next_steps'][:1]}")


# ===========================================================================
# Test 5 — MCP server reachability (post-deploy, Railway)
# ===========================================================================

@_SKIP_RAILWAY
@pytest.mark.asyncio
async def test_mcp_server_reachability():
    """
    Smoke test against deployed Railway instance.
    Skipped unless RAILWAY_URL env var is set.

    Checks:
      GET /health          → {"status": "ok"}
      GET /.well-known/agent.json → {"tools": [...]} with 3 entries
    """
    assert RAILWAY_URL, "RAILWAY_URL not set"

    async with httpx.AsyncClient(timeout=15.0) as client:
        # --- /health ---
        health_resp = await client.get(f"{RAILWAY_URL}/health")
        assert health_resp.status_code == 200, (
            f"/health returned HTTP {health_resp.status_code}: {health_resp.text}"
        )
        health_data = health_resp.json()
        assert health_data.get("status") == "ok", (
            f"Expected status='ok', got: {health_data}"
        )
        print(f"\n  ✓ /health → {health_data}")

        # --- /.well-known/agent.json ---
        agent_resp = await client.get(f"{RAILWAY_URL}/.well-known/agent.json")
        assert agent_resp.status_code == 200, (
            f"/.well-known/agent.json returned HTTP {agent_resp.status_code}"
        )
        agent_data = agent_resp.json()
        # Agent card uses "skills" key (A2A v1 spec)
        skills = agent_data.get("skills", [])
        assert len(skills) == 4, (
            f"Expected 4 skills in agent.json, got {len(skills)}: "
            f"{[s.get('name') for s in skills]}"
        )
        skill_names = {s["name"] for s in skills}
        assert skill_names == {
            "analyze_denial",
            "fetch_clinical_evidence",
            "gap_analysis",
            "check_claim_policy",
        }, f"Unexpected skill names: {skill_names}"
        print(f"  \u2713 agent.json \u2192 {len(skills)} skills found")


# ===========================================================================
# pytest entry point
# ===========================================================================

if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
