"""
fetch_clinical_evidence — MCP Tool (Day 3 build)

Queries FHIR sandbox for patient records based on denial type.
Maps denial_type → correct FHIR resources to retrieve.
"""

# Placeholder — implementation lands Day 3


async def fetch_clinical_evidence(
    denial_type: str, patient_id: str, date_of_service: str
) -> dict:
    """Stub: will be replaced with FHIR-querying evidence fetcher."""
    raise NotImplementedError("fetch_clinical_evidence is scheduled for Day 3")
