# DenialGPT Test Playbook

**6 patients · 9 test cases · All denial types covered**

---

## Setup

Before any patient-specific test, click **FHIR Context → Generate Token → select DenialGPT**.
This attaches FHIR credentials to the A2A call so DenialGPT runs the full tool chain.

Toggle **Show Tool calls** ON to verify each tool fires.

---

## Patient Roster

| # | Name | Denial Type | CARC | Expected Verdict |
|---|------|-------------|------|-----------------|
| 1 | Michael Torres | Medical Necessity | 50 | **STRONG** |
| 2 | Sarah Chen | Medical Necessity | 50 | **DO NOT APPEAL** + write-off memo |
| 3 | James Washington | Medical Necessity | 50 | **WEAK** (outside PT missing) |
| 4 | Robert Kim | Coding Error | 4 | **WEAK** (wrong CPT billed) |
| 5 | Maria Rodriguez | Untimely Filing | 29 | **DO NOT APPEAL** (process failure) |
| 6 | Elena Vasquez | — | — | Prevention **HIGH** risk |

---

## TEST CASE 1 — Michael Torres · Medical Necessity · STRONG

**Seed data:** 8 PT sessions (Apr–May 2026), two Ibuprofen prescriptions, orthopedic consultation note, KL Grade III X-ray.

**Prompt:**
```
Patient Michael Torres has received a denial for right knee total knee arthroplasty
(CPT 27447, ICD-10 M17.11) from Aetna. CARC code 50 — medical necessity not
established. Appeal deadline is 60 days. Please analyze this denial and assess
appeal viability.
```

**Expected DenialGPT output:**
- `denial_type`: Medical Necessity
- `carc_code`: 50
- `root_cause.category`: DOCUMENTATION_GAP
- `appeal_viability`: **STRONG**
- `evidence_found`: PT records (CPT 97110, 8 sessions), Ibuprofen x2 prescriptions, orthopedic note
- `evidence_missing`: Functional outcome scores (KOOS/WOMAC), formal pain scale
- `payer_intelligence.appeal_win_rate`: 38% (Aetna TKA / M17.11 historical)
- `writeoff_memo`: null

---

## TEST CASE 2 — Sarah Chen · Medical Necessity · DO NOT APPEAL

**Seed data:** Single Ibuprofen prescription (not refilled), no PT, no imaging, no consult note.

**Prompt:**
```
Sarah Chen received a denial for right knee arthroplasty (CPT 27447, ICD-10
M17.11) from Aetna, CARC 50. Please run a full gap analysis and determine
whether to appeal.
```

**Expected DenialGPT output:**
- `denial_type`: Medical Necessity
- `carc_code`: 50
- `root_cause.category`: CLINICAL_CRITERIA_UNMET
- `appeal_viability`: **DO NOT APPEAL**
- `evidence_found`: Diagnosis M17.11 on record
- `evidence_missing`: PT records, NSAID refill, functional scores, imaging, orthopedic consult
- `writeoff_memo`: populated — patient, denial_date, policy_basis citing Aetna TKA LCD

---

## TEST CASE 3 — James Washington · Medical Necessity · WEAK

**Seed data:** Vague orthopedic note (05/01/2026) referencing outside PT, no structured PT records in FHIR, Ibuprofen x2 prescriptions.

**Prompt:**
```
James Washington has a denial for right knee MRI (CPT 73721, ICD-10 M17.11)
from Aetna, CARC 50. Vague orthopedic note on file, physical therapy done at
outside facility not in records. Analyze and advise.
```

**Expected DenialGPT output:**
- `denial_type`: Medical Necessity
- `carc_code`: 50
- `root_cause.category`: DOCUMENTATION_GAP
- `appeal_viability`: **WEAK**
- `evidence_found`: Orthopedic note (05/01/2026), diagnosis M17.11, exam findings (effusion, ROM 100°)
- `evidence_missing`: Outside PT records, PT duration/outcomes, specific MRI clinical justification
- `next_steps`: includes "obtain PT records from outside facility" and "physician addendum"
- `writeoff_memo`: null

---

## TEST CASE 4 — Robert Kim · Coding Error · WEAK

**Seed data:** 12 PT sessions, 3 NSAID prescriptions, operative note documenting CPT 27446 (unicompartmental) but claim billed as CPT 27447 (total knee).

**Prompt:**
```
Robert Kim received a denial for right knee arthroplasty from Aetna, CARC code 4
— service inconsistent with the procedure on file. The claim was submitted as
CPT 27447 (total knee arthroplasty). Operative note documents a unicompartmental
procedure was actually performed. Please analyze this denial.
```

**Expected DenialGPT output:**
- `denial_type`: Coding Error
- `carc_code`: 4
- `root_cause.category`: CODING_ERROR
- `appeal_viability`: **WEAK**
- `evidence_found`: Operative note clearly documenting CPT 27446, complete conservative therapy
- `evidence_missing`: Corrected claim with CPT 27446; modifier confirming laterality
- `next_steps`: Resubmit with CPT 27446; no appeal needed — corrected resubmission
- `writeoff_memo`: null

---

## TEST CASE 5 — Maria Rodriguez · Untimely Filing · DO NOT APPEAL

**Seed data:** Complete clinical documentation (10 PT sessions, 2 NSAID prescriptions, orthopedic consult, prior auth obtained). Billing note documenting claim filed 100 days post-service vs. 90-day Aetna deadline.

**Prompt:**
```
Maria Rodriguez received a denial from Aetna for right knee MRI (CPT 73721,
ICD-10 M17.11), CARC code 29 — untimely filing. Date of service was
October 1, 2025. The claim was submitted January 9, 2026. Aetna's filing
deadline is 90 days. Clinical documentation is complete. Analyze this denial.
```

**Expected DenialGPT output:**
- `denial_type`: Untimely Filing
- `carc_code`: 29
- `root_cause.category`: PROCESS_FAILURE
- `appeal_viability`: **DO NOT APPEAL**
- `reasoning`: Filing deadline missed by 10 days — procedural bar, not clinical; appeal rights do not apply
- `evidence_found`: Complete clinical documentation, prior auth on record
- `evidence_missing`: Timely claim submission (irrecoverable)
- `writeoff_memo`: populated — policy_basis citing Aetna timely filing policy, recommendation to write off and implement filing deadline controls

---

## TEST CASE 6 — Elena Vasquez · Prevention HIGH Risk

**No FHIR context needed — pre-submission check.**

**Seed data:** 4 PT sessions only (< 6-week threshold), single NSAID prescription (not refilled), no imaging, 2 months since onset.

**Prompt:**
```
Before submitting a claim for CPT 27447 (total knee arthroplasty), ICD-10
M17.11, payer Aetna, outpatient — what are the denial risks and what should
be done before submission?
```

**Expected DenialGPT output (check_claim_policy):**
- `overall_risk`: **HIGH**
- `risk_flags`: includes insufficient PT duration (< 6 weeks), no NSAID refill, no prior auth obtained, no weight-bearing imaging documented
- `policy_references`: Aetna Clinical Policy Bulletin — Knee Arthroplasty, CMS LCD L36575
- `payer_intelligence`: Aetna TKA/M17.11 — denial rate 54%, top reason "insufficient conservative therapy < 3 months"
- `recommended_fixes`: complete 3 months conservative therapy, refill NSAID, obtain weight-bearing X-ray, submit prior auth before scheduling

---

## TEST CASE 7 — Degraded Mode (No Clinical Context)

**Prompt (no FHIR context, short message):**
```
I have a claim denied with CARC 50. Can you help?
```

**Expected DenialGPT output:**
- Runs `analyze_denial` only (Path C — message too short for clinical content detection)
- Returns denial type, evidence required list, appeal deadline
- Includes fallback message: *"No FHIR patient context provided. Select a patient in Prompt Opinion to run the full gap analysis."*
- Does NOT run gap_analysis — `appeal_viability` not present in response

---

## TEST CASE 8 — Prevention LOW Risk (Clean Submission)

**No FHIR context needed.**

**Prompt:**
```
Before submitting a claim for CPT 73721 (knee MRI without contrast), ICD-10
M23.200 (derangement of anterior horn of medial meniscus), payer Aetna,
outpatient — what are the denial risks?
```

**Expected DenialGPT output:**
- `overall_risk`: **LOW** or **MEDIUM**
- Meniscal tear is a strong indication for MRI — less conservative therapy required than osteoarthritis
- `payer_intelligence`: Aetna/73721/M23.200 entry from PAYER_PATTERNS
- `recommended_fixes`: obtain prior auth, document acute onset and mechanism

---

## TEST CASE 9 — Coding Error + Correct Resubmission Guidance

**Prompt (no patient context needed):**
```
We submitted CPT 27447 for a right knee replacement but the denial came back
as CARC 97 — procedure not paid, included in global period of another procedure.
The patient had an arthroscopy (CPT 29881) performed 15 days earlier at the
same facility. How should we analyze this denial?
```

**Expected DenialGPT output:**
- `denial_type`: Coding Error
- `carc_code`: 97
- `root_cause.category`: CODING_ERROR
- `reasoning`: TKA within global period of prior arthroscopy triggers CARC 97 — requires -78 modifier (return to OR) or separate claim with documentation of distinct surgical decision
- `next_steps`: Resubmit 27447 with modifier -78 if unplanned return to OR; alternatively escalate to payer for manual review with operative notes for both procedures

---

## Seeding Extended Patients

```powershell
cd "C:\Users\srini\Desktop\Stuff\Agents Assemble\Codebase\denialgpt"

python -m patients.seed_patients_extended `
    --fhir-url https://fhir.promptopinion.ai/r4 `
    --token YOUR_FHIR_TOKEN `
    --verify
```

This appends `KIM_PATIENT_ID`, `RODRIGUEZ_PATIENT_ID`, `VASQUEZ_PATIENT_ID` to `.env.promptopinion`.

---

## Coverage Matrix

| Test | Denial Type | CARC | Verdict | Root Cause | Payer Intel | Write-off Memo |
|------|-------------|------|---------|------------|-------------|----------------|
| TC1 Torres | Medical Necessity | 50 | STRONG | DOCUMENTATION_GAP | ✅ | ✗ |
| TC2 Chen | Medical Necessity | 50 | DO NOT APPEAL | CLINICAL_CRITERIA_UNMET | ✅ | ✅ |
| TC3 Washington | Medical Necessity | 50 | WEAK | DOCUMENTATION_GAP | ✅ | ✗ |
| TC4 Kim | Coding Error | 4 | WEAK | CODING_ERROR | ✗ | ✗ |
| TC5 Rodriguez | Untimely Filing | 29 | DO NOT APPEAL | PROCESS_FAILURE | ✗ | ✅ |
| TC6 Vasquez | Prevention | — | HIGH | — | ✅ | — |
| TC7 Degraded | Medical Necessity | 50 | (partial) | — | ✗ | ✗ |
| TC8 Prevention LOW | Prevention | — | LOW/MED | — | ✅ | — |
| TC9 Global Period | Coding Error | 97 | WEAK | CODING_ERROR | ✗ | ✗ |
