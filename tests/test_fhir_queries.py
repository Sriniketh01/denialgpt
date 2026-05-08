"""
Smoke tests for FHIR client connectivity.

These tests assume a FHIR sandbox is running at FHIR_BASE_URL with seeded patients.
Set DEV_PATIENT_ID in .env to a valid Patient 1 (Michael Torres) ID before running.

Usage:
    cd denialgpt
    pytest tests/test_fhir_queries.py -v
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from fhir.client import FHIRClient

# Skip all tests if no patient ID is configured
PATIENT_ID = os.getenv("DEV_PATIENT_ID", "")
SKIP_REASON = "Set DEV_PATIENT_ID in .env to run FHIR integration tests"


@pytest_asyncio.fixture
async def fhir_client():
    """Provide an async FHIR client scoped to the test."""
    client = FHIRClient(dev_mode=True)
    yield client
    await client.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not PATIENT_ID, reason=SKIP_REASON)
async def test_get_patient(fhir_client: FHIRClient):
    """Patient resource should be returned with correct resourceType."""
    patient = await fhir_client.get_patient(PATIENT_ID)
    assert patient["resourceType"] == "Patient"
    assert patient["id"] == PATIENT_ID


@pytest.mark.asyncio
@pytest.mark.skipif(not PATIENT_ID, reason=SKIP_REASON)
async def test_get_conditions(fhir_client: FHIRClient):
    """Patient 1 should have at least one Condition (M17.11)."""
    conditions = await fhir_client.get_conditions(PATIENT_ID)
    assert len(conditions) >= 1
    assert conditions[0]["resourceType"] == "Condition"


@pytest.mark.asyncio
@pytest.mark.skipif(not PATIENT_ID, reason=SKIP_REASON)
async def test_get_medications(fhir_client: FHIRClient):
    """Patient 1 should have MedicationRequest resources."""
    meds = await fhir_client.get_medications(PATIENT_ID)
    assert len(meds) >= 1
    assert all(m["resourceType"] == "MedicationRequest" for m in meds)


@pytest.mark.asyncio
@pytest.mark.skipif(not PATIENT_ID, reason=SKIP_REASON)
async def test_get_procedures(fhir_client: FHIRClient):
    """Patient 1 should have PT Procedure records."""
    procedures = await fhir_client.get_procedures(PATIENT_ID)
    assert len(procedures) >= 1
    assert procedures[0]["resourceType"] == "Procedure"


@pytest.mark.asyncio
@pytest.mark.skipif(not PATIENT_ID, reason=SKIP_REASON)
async def test_get_documents(fhir_client: FHIRClient):
    """Patient 1 should have a DocumentReference (ortho note)."""
    docs = await fhir_client.get_documents(PATIENT_ID)
    assert len(docs) >= 1
    assert docs[0]["resourceType"] == "DocumentReference"


@pytest.mark.asyncio
@pytest.mark.skipif(not PATIENT_ID, reason=SKIP_REASON)
async def test_fetch_all_for_denial(fhir_client: FHIRClient):
    """Bulk fetch should return a dict keyed by resource type."""
    results = await fhir_client.fetch_all_for_denial(
        PATIENT_ID,
        resource_types=["Condition", "Procedure", "MedicationRequest", "DocumentReference"],
    )
    assert "Condition" in results
    assert "Procedure" in results
    assert isinstance(results["Condition"], list)
