"""
Extended Patient Seed Script — adds 3 new demo patients to the FHIR R4 sandbox.

Patient 4: Robert Kim      — Coding Error (CARC 4, WEAK — wrong CPT billed, fixable)
Patient 5: Maria Rodriguez — Untimely Filing (CARC 29, DO NOT APPEAL — process failure)
Patient 6: Elena Vasquez   — Prevention HIGH risk (insufficient conservative therapy)

All three patients use orthopedics + Aetna, consistent with the core demo scenario.

Usage:
    python -m patients.seed_patients_extended \\
        --fhir-url https://fhir.promptopinion.ai/r4 \\
        --token YOUR_ACCESS_TOKEN

    python -m patients.seed_patients_extended --verify

Appends new patient IDs to .env.promptopinion.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

# Re-use all resource builders from the original seed script
from patients.seed_patients import (
    condition_resource,
    document_reference_resource,
    medication_request_resource,
    post_resource,
    procedure_resource,
    patient_resource,
    verify_resource,
)

load_dotenv()


# ---------------------------------------------------------------------------
# Patient 4: Robert Kim — CODING ERROR (CARC 4) — WEAK
#
# Scenario: TKA claim billed as CPT 27447 (total knee arthroplasty) but the
# procedure performed was a unicompartmental (partial) knee replacement, which
# maps to CPT 27446.  Clinical documentation fully supports the procedure —
# the denial is purely a CPT mismatch, not a clinical necessity issue.
#
# Expected verdict: WEAK — resubmit with corrected CPT 27446.
# Expected root_cause: CODING_ERROR
# ---------------------------------------------------------------------------

async def seed_patient_4(client: httpx.AsyncClient, verify: bool = False) -> str:
    print("\n=== Patient 4: Robert Kim (CODING ERROR — WEAK) ===")

    patient = await post_resource(
        client, "Patient",
        patient_resource("Kim", "Robert", "male", "1955-04-22"),
    )
    pid = patient["id"]
    resources_created: list[tuple[str, str]] = [("Patient", pid)]

    # Condition: primary osteoarthritis, right knee (medial compartment only)
    c = await post_resource(
        client, "Condition",
        condition_resource(
            pid, "M17.11",
            "Primary osteoarthritis, right knee — medial compartment",
            onset_date="2024-06-01",
        ),
    )
    resources_created.append(("Condition", c["id"]))

    # Medications: 3 NSAID prescriptions — well-documented conservative trial
    for authored, dosage in [
        ("2025-09-15", "Naproxen 500mg PO BID with food — initial prescription"),
        ("2025-11-01", "Naproxen 500mg PO BID — refill, continued pain"),
        ("2026-01-10", "Celecoxib 200mg PO daily — switched due to GI side effects"),
    ]:
        m = await post_resource(
            client, "MedicationRequest",
            medication_request_resource(pid, "NSAID therapy", authored_on=authored, dosage_text=dosage),
        )
        resources_created.append(("MedicationRequest", m["id"]))

    # Physical therapy: 12 sessions over 3 months
    pt = await post_resource(
        client, "Procedure",
        procedure_resource(
            pid, "97110",
            "Therapeutic Exercises — Knee Strengthening",
            performed_start="2025-10-01",
            performed_end="2025-12-20",
            note_text=(
                "12 PT sessions completed. Patient achieved minimal improvement in "
                "pain and function. ROM remains limited to medial compartment loading."
            ),
        ),
    )
    resources_created.append(("Procedure", pt["id"]))

    # The actual procedure performed: unicompartmental knee (27446)
    # The claim was incorrectly submitted as 27447 (total knee)
    proc = await post_resource(
        client, "Procedure",
        procedure_resource(
            pid, "27446",
            "Arthroplasty, knee, condyle and plateau; medial AND lateral compartments",
            performed_start="2026-03-15",
            performed_end="2026-03-15",
            note_text=(
                "RIGHT KNEE UNICOMPARTMENTAL ARTHROPLASTY (medial compartment). "
                "Intraoperative findings confirm isolated medial compartment disease. "
                "Lateral compartment and patellofemoral joint preserved. "
                "Oxford Phase 3 implant placed. Procedure: CPT 27446."
            ),
        ),
    )
    resources_created.append(("Procedure", proc["id"]))

    # Operative note documenting CPT 27446 (not 27447)
    d = await post_resource(
        client, "DocumentReference",
        document_reference_resource(
            pid,
            description="Operative Note — Right Knee Unicompartmental Arthroplasty",
            date="2026-03-15",
            content_text=(
                "OPERATIVE NOTE\n"
                "Patient: Robert Kim  DOB: 04/22/1955\n"
                "Date of Surgery: 03/15/2026\n\n"
                "PREOPERATIVE DIAGNOSIS: Right knee osteoarthritis, medial compartment (M17.11)\n"
                "POSTOPERATIVE DIAGNOSIS: Same\n"
                "PROCEDURE PERFORMED: Right knee unicompartmental (medial) arthroplasty\n"
                "CPT CODE: 27446\n\n"
                "OPERATIVE FINDINGS: Isolated medial compartment disease with Kellgren-Lawrence "
                "Grade IV changes. Lateral compartment and patellofemoral joint intact. "
                "Appropriate candidate for unicompartmental replacement.\n\n"
                "PROCEDURE DETAILS: Standard medial approach. Oxford Phase 3 mobile-bearing "
                "unicompartmental implant placed. Ligaments intact. Wound closed in layers.\n\n"
                "NOTE: Claim billed as CPT 27447 (total knee arthroplasty) in error. "
                "Correct code is CPT 27446 (unicompartmental arthroplasty). "
                "Corrected claim should be resubmitted."
            ),
        ),
    )
    resources_created.append(("DocumentReference", d["id"]))

    if verify:
        print("  --- Verifying resources ---")
        for rt, rid in resources_created:
            await verify_resource(client, rt, rid)

    return pid


# ---------------------------------------------------------------------------
# Patient 5: Maria Rodriguez — UNTIMELY FILING (CARC 29) — DO NOT APPEAL
#
# Scenario: Right knee MRI (CPT 73721) was performed 2025-10-01. The claim
# was not submitted until 2026-01-09 — 100 days post-service. Aetna's filing
# deadline is 90 days from date of service for out-of-network providers.
# Clinical documentation is complete and would support the MRI medically, but
# the filing window has permanently closed.
#
# Expected verdict: DO NOT APPEAL
# Expected root_cause: PROCESS_FAILURE
# ---------------------------------------------------------------------------

async def seed_patient_5(client: httpx.AsyncClient, verify: bool = False) -> str:
    print("\n=== Patient 5: Maria Rodriguez (UNTIMELY FILING — DO NOT APPEAL) ===")

    patient = await post_resource(
        client, "Patient",
        patient_resource("Rodriguez", "Maria", "female", "1971-08-30"),
    )
    pid = patient["id"]
    resources_created: list[tuple[str, str]] = [("Patient", pid)]

    # Condition: primary osteoarthritis, right knee
    c = await post_resource(
        client, "Condition",
        condition_resource(
            pid, "M17.11",
            "Primary osteoarthritis, right knee",
            onset_date="2025-04-01",
        ),
    )
    resources_created.append(("Condition", c["id"]))

    # Medication: NSAIDs — strong conservative therapy documented
    for authored, dosage in [
        ("2025-05-20", "Ibuprofen 800mg PO TID — initial NSAID trial"),
        ("2025-07-14", "Ibuprofen 800mg PO TID — refill, ongoing pain"),
    ]:
        m = await post_resource(
            client, "MedicationRequest",
            medication_request_resource(pid, "Ibuprofen 800mg tablet", authored_on=authored, dosage_text=dosage),
        )
        resources_created.append(("MedicationRequest", m["id"]))

    # Physical therapy: 10 sessions
    pt = await post_resource(
        client, "Procedure",
        procedure_resource(
            pid, "97110",
            "Therapeutic Exercises — Right Knee",
            performed_start="2025-06-01",
            performed_end="2025-08-10",
            note_text="10 PT sessions completed. Limited functional improvement. Pain persists with weight-bearing.",
        ),
    )
    resources_created.append(("Procedure", pt["id"]))

    # The MRI itself (performed 2025-10-01)
    mri = await post_resource(
        client, "Procedure",
        procedure_resource(
            pid, "73721",
            "MRI Right Knee Without Contrast",
            performed_start="2025-10-01",
            performed_end="2025-10-01",
            note_text="MRI performed 10/01/2025. Claim submitted 01/09/2026 (100 days post-service). Aetna filing deadline: 90 days.",
        ),
    )
    resources_created.append(("Procedure", mri["id"]))

    # Orthopedic note supporting MRI medical necessity (good documentation)
    d1 = await post_resource(
        client, "DocumentReference",
        document_reference_resource(
            pid,
            description="Orthopedic Consultation — Right Knee MRI Order",
            date="2025-09-28",
            content_text=(
                "ORTHOPEDIC CONSULTATION NOTE\n"
                "Patient: Maria Rodriguez  DOB: 08/30/1971\n"
                "Date: 09/28/2025\n\n"
                "CHIEF COMPLAINT: Right knee pain, progressive worsening.\n\n"
                "HISTORY: Patient has undergone 10 PT sessions and 3+ months of NSAID therapy "
                "with inadequate relief. Radiographs show KL Grade III medial compartment "
                "changes. MRI ordered to evaluate for concurrent meniscal pathology or "
                "loose bodies prior to surgical planning.\n\n"
                "ASSESSMENT: Right knee osteoarthritis (M17.11) with conservative therapy failure.\n\n"
                "PLAN: Right knee MRI without contrast (CPT 73721) ordered for surgical planning. "
                "Prior authorization obtained — Auth #AE-2025-78834."
            ),
        ),
    )
    resources_created.append(("DocumentReference", d1["id"]))

    # Billing note documenting the untimely filing error
    d2 = await post_resource(
        client, "DocumentReference",
        document_reference_resource(
            pid,
            description="Billing Note — Untimely Filing Error",
            date="2026-01-15",
            content_text=(
                "BILLING DEPARTMENT NOTE\n"
                "Patient: Maria Rodriguez\n"
                "Date of Service: 10/01/2025 (Right Knee MRI, CPT 73721)\n"
                "Claim Submission Date: 01/09/2026\n"
                "Days Post-Service: 100 days\n\n"
                "ISSUE: Aetna timely filing requirement for this plan is 90 days from date "
                "of service. Claim was submitted 10 days outside the filing window.\n\n"
                "CARC 29 denial received 01/14/2026.\n\n"
                "ROOT CAUSE: Claim was routed incorrectly to secondary billing queue following "
                "office transition. Filing deadline was missed.\n\n"
                "NOTE: Clinical documentation fully supports medical necessity. Denial is "
                "solely due to untimely filing — not clinical criteria. Appeal rights are "
                "extremely limited given the procedural nature of the denial."
            ),
        ),
    )
    resources_created.append(("DocumentReference", d2["id"]))

    if verify:
        print("  --- Verifying resources ---")
        for rt, rid in resources_created:
            await verify_resource(client, rt, rid)

    return pid


# ---------------------------------------------------------------------------
# Patient 6: Elena Vasquez — PREVENTION HIGH RISK
#
# Scenario: TKA (CPT 27447, M17.11) is being planned for submission to Aetna.
# Patient has minimal conservative therapy on record — only 4 PT sessions
# (< 6 weeks) and a single NSAID prescription that was never refilled.
# No imaging on file. Submitting this claim without prior auth or additional
# documentation will almost certainly result in a CARC 50 denial.
#
# Used for: Pre-submission prevention test (Scenario 6)
# Expected prevention result: HIGH denial risk
# ---------------------------------------------------------------------------

async def seed_patient_6(client: httpx.AsyncClient, verify: bool = False) -> str:
    print("\n=== Patient 6: Elena Vasquez (PREVENTION HIGH RISK) ===")

    patient = await post_resource(
        client, "Patient",
        patient_resource("Vasquez", "Elena", "female", "1962-12-05"),
    )
    pid = patient["id"]
    resources_created: list[tuple[str, str]] = [("Patient", pid)]

    # Condition: primary osteoarthritis, right knee
    c = await post_resource(
        client, "Condition",
        condition_resource(
            pid, "M17.11",
            "Primary osteoarthritis, right knee",
            onset_date="2026-01-15",
        ),
    )
    resources_created.append(("Condition", c["id"]))

    # Only 1 NSAID prescription — never refilled
    m = await post_resource(
        client, "MedicationRequest",
        medication_request_resource(
            pid,
            "Ibuprofen 600mg tablet",
            authored_on="2026-02-10",
            dosage_text="600mg PO TID — initial trial, not refilled",
        ),
    )
    resources_created.append(("MedicationRequest", m["id"]))

    # Only 4 PT sessions (< 6-week Aetna threshold)
    pt = await post_resource(
        client, "Procedure",
        procedure_resource(
            pid, "97110",
            "Therapeutic Exercises — Right Knee (initial trial)",
            performed_start="2026-02-20",
            performed_end="2026-03-10",
            note_text=(
                "4 PT sessions completed (2/20, 2/27, 3/06, 3/10). "
                "Patient discontinued — states pain is manageable but wants surgical option. "
                "Aetna requires minimum 6 weeks (typically 12+ sessions) of PT before TKA approval."
            ),
        ),
    )
    resources_created.append(("Procedure", pt["id"]))

    # Orthopedic note — planning TKA without sufficient conservative therapy
    d = await post_resource(
        client, "DocumentReference",
        document_reference_resource(
            pid,
            description="Orthopedic Note — TKA Surgical Planning",
            date="2026-03-20",
            content_text=(
                "ORTHOPEDIC NOTE\n"
                "Patient: Elena Vasquez  DOB: 12/05/1962\n"
                "Date: 03/20/2026\n\n"
                "CHIEF COMPLAINT: Right knee pain — patient requesting surgical evaluation.\n\n"
                "HISTORY: Patient reports 2 months of right knee pain. Has tried ibuprofen "
                "(one prescription, self-discontinued) and attended 4 PT sessions before "
                "stopping. Radiographs taken today show moderate joint space narrowing.\n\n"
                "EXAMINATION: ROM 105 degrees flexion. Mild crepitus. Mild medial tenderness.\n\n"
                "ASSESSMENT: Right knee osteoarthritis, M17.11.\n\n"
                "PLAN: Patient interested in surgical intervention. Scheduling TKA (CPT 27447). "
                "Prior authorization to be submitted to Aetna.\n\n"
                "NOTE: Patient has not completed Aetna-required conservative therapy duration. "
                "Prior authorization submission at risk."
            ),
        ),
    )
    resources_created.append(("DocumentReference", d["id"]))

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
        description="Seed 3 extended demo patients (4-6) into a FHIR R4 sandbox"
    )
    parser.add_argument(
        "--fhir-url",
        default=os.getenv("FHIR_BASE_URL", "http://localhost:8080/fhir"),
        help="FHIR server base URL",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("DEV_ACCESS_TOKEN", "dev-token"),
        help="FHIR bearer token",
    )
    parser.add_argument("--verify", action="store_true",
                        help="Read back each resource after creation")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    fhir_url = args.fhir_url.rstrip("/")
    token = args.token

    print(f"FHIR Base URL: {fhir_url}")
    print(f"Access Token:  {token[:8]}..." if len(token) > 8 else f"Access Token: {token}")

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
            pid4 = await seed_patient_4(client, verify=args.verify)
            pid5 = await seed_patient_5(client, verify=args.verify)
            pid6 = await seed_patient_6(client, verify=args.verify)
        except httpx.HTTPStatusError as e:
            print(f"\nFHIR server error: {e.response.status_code} — {e.response.text}")
            sys.exit(1)
        except httpx.ConnectError:
            print(f"\nCould not connect to FHIR server at {fhir_url}")
            sys.exit(1)

    print("\n" + "=" * 60)
    print("SEED COMPLETE — Extended Patient IDs:")
    print(f"  KIM_PATIENT_ID={pid4}       # Robert Kim (CODING ERROR — WEAK)")
    print(f"  RODRIGUEZ_PATIENT_ID={pid5}  # Maria Rodriguez (UNTIMELY FILING — DO NOT APPEAL)")
    print(f"  VASQUEZ_PATIENT_ID={pid6}    # Elena Vasquez (PREVENTION HIGH RISK)")
    print("=" * 60)

    # Append to .env.promptopinion
    env_file = Path(__file__).resolve().parent.parent / ".env.promptopinion"
    additions = (
        f"\n# Extended patients — seed_patients_extended.py\n"
        f"KIM_PATIENT_ID={pid4}\n"
        f"RODRIGUEZ_PATIENT_ID={pid5}\n"
        f"VASQUEZ_PATIENT_ID={pid6}\n"
    )
    with open(env_file, "a", encoding="utf-8") as f:
        f.write(additions)
    print(f"\nAppended to {env_file}")


if __name__ == "__main__":
    asyncio.run(main())
