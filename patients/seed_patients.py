"""
Synthetic Patient Seed Script — creates 3 demo patients in a FHIR R4 sandbox.

Patient 1: Michael Torres — STRONG appeal (PT records exist, full conservative mgmt)
Patient 2: Sarah Chen     — DO NOT APPEAL (genuinely insufficient documentation)
Patient 3: James Washington — WEAK / FIXABLE (outside PT, vague notes, needs addendum)

Usage:
    # Using .env defaults
    python -m patients.seed_patients

    # Explicit CLI args (for Prompt Opinion sandbox)
    python -m patients.seed_patients \
        --fhir-url https://fhir.promptopinion.ai/r4 \
        --token YOUR_ACCESS_TOKEN

    # Verify resources after creation
    python -m patients.seed_patients --verify

Prints created resource IDs and writes .env.promptopinion with patient IDs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def post_resource(
    client: httpx.AsyncClient, resource_type: str, resource: dict
) -> dict:
    """POST a FHIR resource and return the created resource with server-assigned ID."""
    resp = await client.post(f"/{resource_type}", json=resource)
    resp.raise_for_status()
    created = resp.json()
    print(f"  Created {resource_type}/{created.get('id', '?')}")
    return created


async def verify_resource(
    client: httpx.AsyncClient, resource_type: str, resource_id: str
) -> bool:
    """GET a resource back and confirm it's queryable."""
    try:
        resp = await client.get(f"/{resource_type}/{resource_id}")
        resp.raise_for_status()
        data = resp.json()
        ok = data.get("resourceType") == resource_type
        status = "OK" if ok else "MISMATCH"
        print(f"  Verify {resource_type}/{resource_id}: {status}")
        return ok
    except Exception as e:
        print(f"  Verify {resource_type}/{resource_id}: FAILED — {e}")
        return False


def patient_resource(
    family: str,
    given: str,
    gender: str,
    birth_date: str,
) -> dict:
    return {
        "resourceType": "Patient",
        "name": [{"family": family, "given": [given], "use": "official"}],
        "gender": gender,
        "birthDate": birth_date,
    }


def condition_resource(
    patient_id: str,
    code: str,
    display: str,
    system: str = "http://hl7.org/fhir/sid/icd-10-cm",
    clinical_status: str = "active",
    onset_date: str | None = None,
) -> dict:
    resource: dict[str, Any] = {
        "resourceType": "Condition",
        "subject": {"reference": f"Patient/{patient_id}"},
        "code": {
            "coding": [{"system": system, "code": code, "display": display}],
            "text": display,
        },
        "clinicalStatus": {
            "coding": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    "code": clinical_status,
                }
            ]
        },
    }
    if onset_date:
        resource["onsetDateTime"] = onset_date
    return resource


def medication_request_resource(
    patient_id: str,
    medication_display: str,
    authored_on: str,
    dosage_text: str,
    status: str = "active",
) -> dict:
    return {
        "resourceType": "MedicationRequest",
        "status": status,
        "intent": "order",
        "subject": {"reference": f"Patient/{patient_id}"},
        "medicationCodeableConcept": {
            "coding": [
                {
                    "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                    "display": medication_display,
                }
            ],
            "text": medication_display,
        },
        "authoredOn": authored_on,
        "dosageInstruction": [{"text": dosage_text}],
    }


def procedure_resource(
    patient_id: str,
    code: str,
    display: str,
    performed_start: str,
    performed_end: str,
    system: str = "http://www.ama-assn.org/go/cpt",
    note_text: str | None = None,
) -> dict:
    resource: dict[str, Any] = {
        "resourceType": "Procedure",
        "status": "completed",
        "subject": {"reference": f"Patient/{patient_id}"},
        "code": {
            "coding": [{"system": system, "code": code, "display": display}],
            "text": display,
        },
        "performedPeriod": {"start": performed_start, "end": performed_end},
    }
    if note_text:
        resource["note"] = [{"text": note_text}]
    return resource


def document_reference_resource(
    patient_id: str,
    description: str,
    date: str,
    content_text: str,
    doc_type_code: str = "11488-4",
    doc_type_display: str = "Consultation Note",
) -> dict:
    import base64

    return {
        "resourceType": "DocumentReference",
        "status": "current",
        "subject": {"reference": f"Patient/{patient_id}"},
        "type": {
            "coding": [
                {
                    "system": "http://loinc.org",
                    "code": doc_type_code,
                    "display": doc_type_display,
                }
            ]
        },
        "description": description,
        "date": date,
        "content": [
            {
                "attachment": {
                    "contentType": "text/plain",
                    "data": base64.b64encode(content_text.encode()).decode(),
                    "title": description,
                }
            }
        ],
    }


# ---------------------------------------------------------------------------
# Patient 1: Michael Torres — STRONG APPEAL
# ---------------------------------------------------------------------------

async def seed_patient_1(client: httpx.AsyncClient, verify: bool = False) -> str:
    print("\n=== Patient 1: Michael Torres (STRONG APPEAL) ===")

    patient = await post_resource(
        client, "Patient",
        patient_resource("Torres", "Michael", "male", "1968-07-15"),
    )
    pid = patient["id"]

    resources_created: list[tuple[str, str]] = [("Patient", pid)]

    # Condition: primary osteoarthritis, right knee
    c = await post_resource(
        client, "Condition",
        condition_resource(
            pid, "M17.11",
            "Primary osteoarthritis, right knee",
            onset_date="2024-11-01",
        ),
    )
    resources_created.append(("Condition", c["id"]))

    # MedicationRequest: Ibuprofen 600mg — initial March 2026
    m1 = await post_resource(
        client, "MedicationRequest",
        medication_request_resource(
            pid,
            "Ibuprofen 600mg tablet",
            authored_on="2026-03-10",
            dosage_text="600mg PO TID with food",
        ),
    )
    resources_created.append(("MedicationRequest", m1["id"]))

    # MedicationRequest: Ibuprofen 600mg — refill April 2026
    m2 = await post_resource(
        client, "MedicationRequest",
        medication_request_resource(
            pid,
            "Ibuprofen 600mg tablet",
            authored_on="2026-04-10",
            dosage_text="600mg PO TID with food — refill",
        ),
    )
    resources_created.append(("MedicationRequest", m2["id"]))

    # DocumentReference: Orthopedic consultation note May 2026
    d = await post_resource(
        client, "DocumentReference",
        document_reference_resource(
            pid,
            description="Orthopedic Consultation Note — Right Knee",
            date="2026-05-05",
            content_text=(
                "ORTHOPEDIC CONSULTATION NOTE\n"
                "Patient: Michael Torres  DOB: 07/15/1968\n"
                "Date: 05/05/2026\n\n"
                "CHIEF COMPLAINT: Right knee pain, worsening over 6 months.\n\n"
                "HISTORY: Patient has been undergoing conservative management for "
                "right knee osteoarthritis (M17.11) since November 2024. He has "
                "completed 8 sessions of physical therapy (April–May 2026) with "
                "limited improvement. NSAID therapy (Ibuprofen 600mg TID) initiated "
                "March 2026 with partial pain relief but continued functional "
                "limitation.\n\n"
                "EXAMINATION: ROM limited to 95 degrees flexion. Crepitus on "
                "extension. Medial joint line tenderness. Negative McMurray.\n\n"
                "IMAGING: Weight-bearing AP/lateral radiographs show Kellgren-"
                "Lawrence Grade III changes with medial compartment narrowing.\n\n"
                "ASSESSMENT: Right knee osteoarthritis with failure of conservative "
                "management. Patient has exhausted NSAID therapy, completed physical "
                "therapy, and continues to have significant functional limitation.\n\n"
                "PLAN: Recommend right knee MRI to evaluate for meniscal pathology "
                "and surgical planning. Prior authorization requested."
            ),
        ),
    )
    resources_created.append(("DocumentReference", d["id"]))

    # Procedure: Physical therapy — 8 sessions April–May 2026
    p = await post_resource(
        client, "Procedure",
        procedure_resource(
            pid,
            code="97110",
            display="Physical Therapy — Therapeutic Exercises",
            performed_start="2026-04-01",
            performed_end="2026-05-15",
            note_text=(
                "8 sessions of supervised therapeutic exercise for right knee "
                "osteoarthritis. Focus on quadriceps strengthening, ROM restoration, "
                "and gait training. Patient showed limited improvement — continued "
                "pain with weight-bearing activities and stair climbing."
            ),
        ),
    )
    resources_created.append(("Procedure", p["id"]))

    if verify:
        print("  --- Verifying resources ---")
        for rt, rid in resources_created:
            await verify_resource(client, rt, rid)

    return pid


# ---------------------------------------------------------------------------
# Patient 2: Sarah Chen — DO NOT APPEAL
# ---------------------------------------------------------------------------

async def seed_patient_2(client: httpx.AsyncClient, verify: bool = False) -> str:
    print("\n=== Patient 2: Sarah Chen (DO NOT APPEAL) ===")

    patient = await post_resource(
        client, "Patient",
        patient_resource("Chen", "Sarah", "female", "1975-03-22"),
    )
    pid = patient["id"]

    resources_created: list[tuple[str, str]] = [("Patient", pid)]

    # Condition: same diagnosis
    c = await post_resource(
        client, "Condition",
        condition_resource(
            pid, "M17.11",
            "Primary osteoarthritis, right knee",
            onset_date="2026-01-15",
        ),
    )
    resources_created.append(("Condition", c["id"]))

    # MedicationRequest: Ibuprofen 600mg — ONE prescription, NOT refilled
    m = await post_resource(
        client, "MedicationRequest",
        medication_request_resource(
            pid,
            "Ibuprofen 600mg tablet",
            authored_on="2026-03-01",
            dosage_text="600mg PO TID with food",
            status="stopped",
        ),
    )
    resources_created.append(("MedicationRequest", m["id"]))

    # No DocumentReference — no ortho notes
    # No Procedure — no PT
    # This is genuinely insufficient. Denial is correct.

    if verify:
        print("  --- Verifying resources ---")
        for rt, rid in resources_created:
            await verify_resource(client, rt, rid)

    return pid


# ---------------------------------------------------------------------------
# Patient 3: James Washington — WEAK / FIXABLE
# ---------------------------------------------------------------------------

async def seed_patient_3(client: httpx.AsyncClient, verify: bool = False) -> str:
    print("\n=== Patient 3: James Washington (WEAK / FIXABLE) ===")

    patient = await post_resource(
        client, "Patient",
        patient_resource("Washington", "James", "male", "1962-11-08"),
    )
    pid = patient["id"]

    resources_created: list[tuple[str, str]] = [("Patient", pid)]

    # Condition: same diagnosis
    c = await post_resource(
        client, "Condition",
        condition_resource(
            pid, "M17.11",
            "Primary osteoarthritis, right knee",
            onset_date="2024-09-20",
        ),
    )
    resources_created.append(("Condition", c["id"]))

    # MedicationRequest: Ibuprofen March 2026
    m1 = await post_resource(
        client, "MedicationRequest",
        medication_request_resource(
            pid,
            "Ibuprofen 600mg tablet",
            authored_on="2026-03-05",
            dosage_text="600mg PO TID with food",
        ),
    )
    resources_created.append(("MedicationRequest", m1["id"]))

    # MedicationRequest: Ibuprofen refill April 2026
    m2 = await post_resource(
        client, "MedicationRequest",
        medication_request_resource(
            pid,
            "Ibuprofen 600mg tablet",
            authored_on="2026-04-05",
            dosage_text="600mg PO TID with food — refill",
        ),
    )
    resources_created.append(("MedicationRequest", m2["id"]))

    # DocumentReference: Vague orthopedic note
    d = await post_resource(
        client, "DocumentReference",
        document_reference_resource(
            pid,
            description="Orthopedic Follow-up Note — Right Knee",
            date="2026-05-01",
            content_text=(
                "ORTHOPEDIC FOLLOW-UP NOTE\n"
                "Patient: James Washington  DOB: 11/08/1962\n"
                "Date: 05/01/2026\n\n"
                "CHIEF COMPLAINT: Right knee pain, ongoing.\n\n"
                "HISTORY: Patient reports continued right knee pain. "
                "Conservative management ongoing. Patient states he has been "
                "doing physical therapy at an outside facility.\n\n"
                "EXAMINATION: Mild effusion. ROM 100 degrees flexion. "
                "Tenderness along medial joint line.\n\n"
                "ASSESSMENT: Right knee osteoarthritis, M17.11. Conservative "
                "management ongoing per patient report.\n\n"
                "PLAN: Continue current treatment. Consider MRI if no "
                "improvement. Will obtain outside PT records."
            ),
        ),
    )
    resources_created.append(("DocumentReference", d["id"]))

    # No Procedure records — patient had PT at outside facility, records not
    # yet in FHIR. This is the "fixable" gap.

    if verify:
        print("  --- Verifying resources ---")
        for rt, rid in resources_created:
            await verify_resource(client, rt, rid)

    return pid


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed 3 demo patients into a FHIR R4 sandbox"
    )
    parser.add_argument(
        "--fhir-url",
        default=os.getenv("FHIR_BASE_URL", "http://localhost:8080/fhir"),
        help="FHIR server base URL (default: FHIR_BASE_URL env var or localhost:8080)",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("DEV_ACCESS_TOKEN", "dev-token"),
        help="FHIR bearer token (default: DEV_ACCESS_TOKEN env var)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Read back each resource after creation to confirm it's queryable",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    fhir_url = args.fhir_url.rstrip("/")
    token = args.token

    print(f"FHIR Base URL: {fhir_url}")
    print(f"Access Token:  {token[:8]}..." if len(token) > 8 else f"Access Token: {token}")
    if args.verify:
        print("Verify mode:   ON")

    async with httpx.AsyncClient(
        base_url=fhir_url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/fhir+json",
            "Accept": "application/fhir+json",
        },
        timeout=30.0,
    ) as client:
        try:
            pid1 = await seed_patient_1(client, verify=args.verify)
            pid2 = await seed_patient_2(client, verify=args.verify)
            pid3 = await seed_patient_3(client, verify=args.verify)
        except httpx.HTTPStatusError as e:
            print(f"\nFHIR server error: {e.response.status_code} — {e.response.text}")
            sys.exit(1)
        except httpx.ConnectError:
            print(f"\nCould not connect to FHIR server at {fhir_url}")
            print("Make sure your FHIR sandbox is running.")
            sys.exit(1)

    # Print summary
    print("\n" + "=" * 60)
    print("SEED COMPLETE — Patient IDs:")
    print(f"  TORRES_PATIENT_ID={{pid1}}     # Michael Torres (STRONG)")
    print(f"  CHEN_PATIENT_ID={{pid2}}       # Sarah Chen (DO NOT APPEAL)")
    print(f"  WASHINGTON_PATIENT_ID={{pid3}}  # James Washington (WEAK/FIXABLE)")
    print("=" * 60)

    # Write .env.promptopinion
    env_file = Path(__file__).resolve().parent.parent / ".env.promptopinion"
    env_content = (
        f"# Prompt Opinion FHIR Sandbox — generated by seed_patients.py\n"
        f"FHIR_BASE_URL={{fhir_url}}\n"
        f"DEV_ACCESS_TOKEN={{token}}\n"
        f"TORRES_PATIENT_ID={{pid1}}\n"
        f"CHEN_PATIENT_ID={{pid2}}\n"
        f"WASHINGTON_PATIENT_ID={{pid3}}\n"
    )
    env_file.write_text(env_content, encoding="utf-8")
    print(f"\nWrote {{env_file}}")
    print("Load with: dotenv -f .env.promptopinion")


if __name__ == "__main__":
    asyncio.run(main())
