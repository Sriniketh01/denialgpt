"""
fetch_clinical_evidence — MCP Tool (Day 3)

Queries FHIR sandbox for patient records based on denial type.
Maps denial_type -> correct FHIR resources to retrieve.
Returns a structured evidence package for gap_analysis.
"""

from __future__ import annotations

import os
from typing import Any

from fhir.client import FHIRClient


# ---------------------------------------------------------------------------
# Denial type -> FHIR resource mapping
# ---------------------------------------------------------------------------

DENIAL_RESOURCE_MAP: dict[str, list[str]] = {
    "Medical Necessity": [
        "Condition",
        "Observation",
        "Procedure",
        "MedicationRequest",
        "DocumentReference",
    ],
    "Coding Error": [
        "Procedure",
        "DocumentReference",
    ],
    "Missing Documentation": [
        "DocumentReference",
        "ExplanationOfBenefit",
    ],
    "Untimely Filing": [
        "ExplanationOfBenefit",
    ],
}


def _summarize_resource(resource: dict) -> dict[str, Any]:
    """Extract key fields from a FHIR resource for LLM consumption."""
    rt = resource.get("resourceType", "Unknown")
    summary: dict[str, Any] = {
        "resourceType": rt,
        "id": resource.get("id"),
    }

    if rt == "Condition":
        code_obj = resource.get("code", {})
        codings = code_obj.get("coding", [])
        summary["code"] = codings[0].get("code") if codings else None
        summary["display"] = code_obj.get("text") or (codings[0].get("display") if codings else None)
        summary["onsetDateTime"] = resource.get("onsetDateTime")
        cs = resource.get("clinicalStatus", {}).get("coding", [])
        summary["clinicalStatus"] = cs[0].get("code") if cs else None

    elif rt == "Procedure":
        code_obj = resource.get("code", {})
        codings = code_obj.get("coding", [])
        summary["code"] = codings[0].get("code") if codings else None
        summary["display"] = code_obj.get("text") or (codings[0].get("display") if codings else None)
        summary["status"] = resource.get("status")
        summary["performedPeriod"] = resource.get("performedPeriod")
        notes = resource.get("note", [])
        summary["notes"] = [n.get("text", "") for n in notes] if notes else []

    elif rt == "MedicationRequest":
        med = resource.get("medicationCodeableConcept", {})
        summary["medication"] = med.get("text") or (
            med.get("coding", [{}])[0].get("display") if med.get("coding") else None
        )
        summary["status"] = resource.get("status")
        summary["authoredOn"] = resource.get("authoredOn")
        dosage = resource.get("dosageInstruction", [])
        summary["dosage"] = dosage[0].get("text") if dosage else None

    elif rt == "DocumentReference":
        summary["description"] = resource.get("description")
        summary["date"] = resource.get("date")
        summary["status"] = resource.get("status")
        content_list = resource.get("content", [])
        if content_list:
            attachment = content_list[0].get("attachment", {})
            summary["title"] = attachment.get("title")
            if attachment.get("contentType") == "text/plain" and attachment.get("data"):
                import base64
                try:
                    summary["text"] = base64.b64decode(attachment["data"]).decode("utf-8")
                except Exception:
                    summary["text"] = "[unable to decode]"

    elif rt == "Observation":
        code_obj = resource.get("code", {})
        codings = code_obj.get("coding", [])
        summary["code"] = codings[0].get("code") if codings else None
        summary["display"] = code_obj.get("text") or (codings[0].get("display") if codings else None)
        summary["value"] = resource.get("valueQuantity") or resource.get("valueString")

    elif rt == "ExplanationOfBenefit":
        summary["status"] = resource.get("status")
        summary["outcome"] = resource.get("outcome")
        summary["created"] = resource.get("created")

    return summary


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

async def run_fetch_clinical_evidence(
    denial_type: str,
    patient_id: str,
    date_of_service: str,
    fhir_base_url: str | None = None,
    access_token: str | None = None,
) -> dict[str, Any]:
    """
    Fetch clinical evidence from FHIR based on denial type.

    Returns a structured evidence package:
        patient_id, denial_type, date_of_service, resources_fetched,
        fhir_resource_ids, evidence (keyed by resource type),
        summary (human-readable), fetch_errors.
    """
    resource_types = DENIAL_RESOURCE_MAP.get(denial_type)
    if resource_types is None:
        return {
            "patient_id": patient_id,
            "denial_type": denial_type,
            "error": f"Unknown denial_type '{denial_type}'. "
                     f"Must be one of: {list(DENIAL_RESOURCE_MAP.keys())}",
            "resources_fetched": [],
            "fhir_resource_ids": [],
            "evidence": {},
            "fetch_errors": [f"Unknown denial type: {denial_type}"],
        }

    base_url = fhir_base_url or os.getenv("FHIR_BASE_URL", "http://localhost:8080/fhir")
    token = access_token or os.getenv("DEV_ACCESS_TOKEN", "dev-token")

    evidence: dict[str, list[dict]] = {}
    fetch_errors: list[str] = []
    all_ids: list[str] = []
    resources_fetched: list[str] = []

    async with FHIRClient(base_url=base_url, access_token=token) as fhir:
        raw_results = await fhir.fetch_all_for_denial(
            patient_id=patient_id,
            resource_types=resource_types,
        )

    for rt, resources in raw_results.items():
        if resources:
            resources_fetched.append(rt)
            summaries = [_summarize_resource(r) for r in resources]
            evidence[rt] = summaries
            all_ids.extend(
                f"{rt}/{r.get('id', '?')}" for r in resources if r.get("id")
            )
        else:
            evidence[rt] = []

    summary_lines = [
        f"Clinical evidence for {denial_type} denial -- Patient {patient_id}",
        f"Date of service: {date_of_service}",
        "",
    ]
    for rt in resource_types:
        items = evidence.get(rt, [])
        summary_lines.append(f"{rt}: {len(items)} record(s) found")
        for item in items:
            desc = (
                item.get("display")
                or item.get("description")
                or item.get("medication")
                or item.get("title")
                or rt
            )
            summary_lines.append(f"  - {desc}")
    summary_lines.append("")
    summary_lines.append(f"Total resources: {len(all_ids)}")

    return {
        "patient_id": patient_id,
        "denial_type": denial_type,
        "date_of_service": date_of_service,
        "resources_fetched": resources_fetched,
        "fhir_resource_ids": all_ids,
        "evidence": evidence,
        "summary": "\n".join(summary_lines),
        "fetch_errors": fetch_errors,
    }
