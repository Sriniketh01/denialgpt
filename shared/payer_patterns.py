"""
payer_patterns — Shared Payer Intelligence Utility (Person B builds, Person A imports)

Hardcoded lookup table of known denial patterns for specific payer + CPT + ICD-10
combinations. Scoped to orthopedics + Aetna only for the MVP demo.

Two key shapes are supported:

  Prevention lookup (3-key):
      (payer, cpt_code, icd10_code)
      Used by check_claim_policy to surface denial risk before submission.

  Post-denial lookup (4-key):
      (payer, cpt_code, icd10_code, carc_code)
      Used by gap_analysis to inject appeal win rates + winning evidence.
      Falls back to the 3-key entry if no 4-key match is found.

Public API
----------
    from shared.payer_patterns import get_payer_pattern

    # Prevention — no CARC code yet
    intel = get_payer_pattern("Aetna", "73721", "M17.11")

    # Post-denial — CARC code known
    intel = get_payer_pattern("Aetna", "73721", "M17.11", carc_code="50")

Pattern dict schema (ALL fields required — Person A's gap_analysis depends on this shape):
    {
        "denial_rate":      str,   # e.g. "68%"
        "top_reason":       str,   # human-readable denial reason
        "appeal_win_rate":  str,   # e.g. "41%"
        "winning_evidence": str,   # what to attach to win the appeal
        "prevention":       str,   # action to take before next submission
    }

Entries
-------
  3-key (prevention):
    (Aetna, 73721, M17.11)  — Knee MRI, primary osteoarthritis right knee
    (Aetna, 27447, M17.11)  — Total knee replacement, osteoarthritis
    (Aetna, 73721, M23.61)  — Knee MRI, medial meniscus tear
    (Aetna, 27130, M16.11)  — Total hip replacement, osteoarthritis right hip
    (Aetna, 73223, M75.1)   — Shoulder MRI, rotator cuff syndrome

  4-key (post-denial, CARC 50):
    (Aetna, 73721, M17.11, 50)  — demo scenario post-denial
    (Aetna, 27447, M17.11, 50)  — TKA post-denial
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Type aliases for readability
# ---------------------------------------------------------------------------
PatternKey3 = tuple[str, str, str]        # (payer, cpt_code, icd10_code)
PatternKey4 = tuple[str, str, str, str]   # (payer, cpt_code, icd10_code, carc_code)

# ---------------------------------------------------------------------------
# PAYER_PATTERNS
#
# Schema must NOT change — Person A's gap_analysis imports this exact shape.
# ---------------------------------------------------------------------------
PAYER_PATTERNS: dict[PatternKey3 | PatternKey4, dict] = {

    # ==========================================================================
    # ENTRY 1 — Knee MRI + Primary Osteoarthritis, Right Knee (DEMO SCENARIO)
    # CPT 73721  |  ICD-10 M17.11  |  Payer: Aetna
    # 3-key: prevention lookup before submission
    # ==========================================================================
    ("Aetna", "73721", "M17.11"): {
        "denial_rate":      "68%",
        "top_reason":       "Prior authorization not obtained",
        "appeal_win_rate":  "41%",
        "winning_evidence": (
            "PT notes (6+ weeks) + physician statement of failed conservative therapy"
        ),
        "prevention": (
            "Always obtain prior auth from Aetna before scheduling outpatient knee MRI "
            "for M17.11. Attach conservative therapy documentation at time of auth request."
        ),
    },

    # --------------------------------------------------------------------------
    # ENTRY 1b — same combination, post-denial CARC 50
    # 4-key: used by gap_analysis when denial reason code is known
    # --------------------------------------------------------------------------
    ("Aetna", "73721", "M17.11", "50"): {
        "denial_rate":      "68%",
        "top_reason":       "Medical necessity not established — conservative therapy not documented",
        "appeal_win_rate":  "41%",
        "winning_evidence": (
            "PT notes documenting 6+ weeks of physical therapy with inadequate response "
            "+ physician letter stating MRI is necessary for surgical planning or to "
            "rule out meniscal/ligamentous pathology not detectable on X-ray"
        ),
        "prevention": (
            "Add prior auth checkpoint to pre-submission workflow for CPT 73721 + Aetna. "
            "Ensure 6+ weeks of conservative therapy (PT or documented NSAID trial) is "
            "in the chart and attached to the auth request before scheduling."
        ),
    },

    # ==========================================================================
    # ENTRY 2 — Total Knee Arthroplasty + Primary Osteoarthritis, Right Knee
    # CPT 27447  |  ICD-10 M17.11  |  Payer: Aetna
    # 3-key: prevention lookup before submission
    # ==========================================================================
    ("Aetna", "27447", "M17.11"): {
        "denial_rate":      "54%",
        "top_reason":       "Insufficient conservative therapy — less than 3 months documented",
        "appeal_win_rate":  "38%",
        "winning_evidence": (
            "PT records (3+ months) + documented NSAID failure "
            "+ weight-bearing X-ray showing joint space narrowing (KL grade 3 or 4) "
            "+ orthopedic surgeon letter of medical necessity"
        ),
        "prevention": (
            "Ensure chart documents 3+ months of conservative therapy (PT + NSAIDs or "
            "corticosteroid injections) before submitting TKA claim to Aetna. Attach "
            "current weight-bearing knee X-ray and surgeon letter of medical necessity. "
            "Prior auth required — do not schedule before auth is confirmed."
        ),
    },

    # --------------------------------------------------------------------------
    # ENTRY 2b — same combination, post-denial CARC 50
    # 4-key: used by gap_analysis when denial reason code is known
    # --------------------------------------------------------------------------
    ("Aetna", "27447", "M17.11", "50"): {
        "denial_rate":      "54%",
        "top_reason":       "Medical necessity not established — conservative therapy duration insufficient",
        "appeal_win_rate":  "38%",
        "winning_evidence": (
            "PT records showing 3+ months of treatment with documented functional decline "
            "+ radiographic evidence of severe joint space narrowing (KL grade 3–4) "
            "+ orthopedic surgeon attestation that conservative treatment has failed "
            "+ patient functional assessment scores (KOOS or WOMAC)"
        ),
        "prevention": (
            "Build a pre-submission checklist for TKA claims: (1) 3+ months PT in chart, "
            "(2) current weight-bearing X-ray with radiology read, (3) surgeon letter, "
            "(4) confirmed prior auth. Missing any one of these causes this denial."
        ),
    },

    # ==========================================================================
    # ENTRY 3 — Knee MRI + Medial Meniscus Tear
    # CPT 73721  |  ICD-10 M23.61  |  Payer: Aetna
    # 3-key: prevention lookup before submission
    # ==========================================================================
    ("Aetna", "73721", "M23.61"): {
        "denial_rate":      "44%",
        "top_reason":       "Prior authorization not obtained",
        "appeal_win_rate":  "52%",
        "winning_evidence": (
            "Clinical notes documenting acute knee injury mechanism or progressive "
            "mechanical symptoms (locking, catching, giving way) + failed 4–6 weeks "
            "conservative management + physician order with clinical justification"
        ),
        "prevention": (
            "Obtain prior auth before ordering knee MRI for M23.61 with Aetna. "
            "Document the injury mechanism and mechanical symptoms clearly in the "
            "clinical note — Aetna accepts MRI sooner for acute traumatic tears "
            "than for degenerative osteoarthritis, but auth is still required."
        ),
    },

    # ==========================================================================
    # ENTRY 4 — Total Hip Arthroplasty + Primary Osteoarthritis, Right Hip
    # CPT 27130  |  ICD-10 M16.11  |  Payer: Aetna
    # 3-key: prevention lookup before submission
    # ==========================================================================
    ("Aetna", "27130", "M16.11"): {
        "denial_rate":      "48%",
        "top_reason":       "Insufficient conservative therapy documentation",
        "appeal_win_rate":  "36%",
        "winning_evidence": (
            "PT records (3+ months) + documented NSAID trial with inadequate response "
            "+ AP pelvis X-ray showing femoral head collapse or severe joint space "
            "narrowing + surgeon letter of medical necessity + BMI documentation "
            "if applicable (Aetna requires BMI < 40 or clinical exception for THA)"
        ),
        "prevention": (
            "Before submitting THA claim to Aetna: confirm 3+ months conservative "
            "therapy is documented, obtain current AP pelvis X-ray with radiology read, "
            "attach surgeon letter, and verify patient's BMI is under Aetna's threshold "
            "(< 40) or prepare a clinical exception letter. Prior auth is mandatory."
        ),
    },

    # ==========================================================================
    # ENTRY 5 — Shoulder MRI + Rotator Cuff Syndrome
    # CPT 73223  |  ICD-10 M75.1  |  Payer: Aetna
    # 3-key: prevention lookup before submission
    # ==========================================================================
    ("Aetna", "73223", "M75.1"): {
        "denial_rate":      "57%",
        "top_reason":       "Medical necessity not established — no documented conservative therapy trial",
        "appeal_win_rate":  "45%",
        "winning_evidence": (
            "PT notes (4–6 weeks of shoulder-specific therapy) + documented failure of "
            "NSAIDs or corticosteroid injection + clinical note documenting functional "
            "limitation and persistent pain + physician attestation MRI needed to "
            "evaluate full-thickness tear prior to surgical intervention"
        ),
        "prevention": (
            "Aetna requires 4–6 weeks of conservative therapy before approving shoulder "
            "MRI for M75.1. Document PT, NSAID use, and any injection in the chart "
            "before ordering. Prior auth required — include clinical justification "
            "specifying whether surgical planning is the indication for MRI."
        ),
    },

}


# ---------------------------------------------------------------------------
# Public lookup function
# ---------------------------------------------------------------------------

def get_payer_pattern(
    payer: str,
    cpt_code: str,
    icd10_code: str,
    carc_code: str | None = None,
) -> dict | None:
    """Return payer pattern intelligence for a given claim combination.

    Lookup order
    ------------
    1. If ``carc_code`` is provided, attempt a 4-key lookup:
       ``(payer, cpt_code, icd10_code, carc_code)``
    2. Fall back to a 3-key lookup:
       ``(payer, cpt_code, icd10_code)``
    3. Return ``None`` if neither key is found.

    Parameters
    ----------
    payer : str
        Insurance payer name, e.g. ``"Aetna"``.
    cpt_code : str
        CPT procedure code, e.g. ``"73721"``.
    icd10_code : str
        ICD-10 diagnosis code, e.g. ``"M17.11"``.
    carc_code : str or None
        CARC denial reason code, e.g. ``"50"``. Optional — omit for
        prevention lookups where the claim has not yet been denied.

    Returns
    -------
    dict or None
        Pattern intelligence dict with keys:
        ``denial_rate``, ``top_reason``, ``appeal_win_rate``,
        ``winning_evidence``, ``prevention``.
        Returns ``None`` if no pattern exists for this combination.

    Examples
    --------
    >>> get_payer_pattern("Aetna", "73721", "M17.11")
    {'denial_rate': '68%', 'top_reason': 'Prior authorization not obtained', ...}

    >>> get_payer_pattern("Aetna", "73721", "M17.11", carc_code="50")
    {'denial_rate': '68%', 'top_reason': 'Medical necessity not established...', ...}

    >>> get_payer_pattern("Aetna", "99999", "Z00.00")
    None
    """
    # 4-key lookup first (post-denial path)
    if carc_code is not None:
        key4: PatternKey4 = (payer, cpt_code, icd10_code, carc_code)
        pattern = PAYER_PATTERNS.get(key4)
        if pattern is not None:
            return pattern

    # 3-key fallback (prevention path, or post-denial with no 4-key entry)
    key3: PatternKey3 = (payer, cpt_code, icd10_code)
    return PAYER_PATTERNS.get(key3)


# ---------------------------------------------------------------------------
# __main__ — verify all entries are readable and schema is intact
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    print("=" * 60)
    print("PAYER_PATTERNS VERIFICATION")
    print(f"  Total entries: {len(PAYER_PATTERNS)}")
    print("=" * 60)

    test_cases: list[tuple] = [
        # Demo scenarios (3-key prevention)
        ("Aetna", "73721", "M17.11",  None),
        ("Aetna", "27447", "M17.11",  None),
        # Demo scenarios (4-key post-denial CARC 50)
        ("Aetna", "73721", "M17.11",  "50"),
        ("Aetna", "27447", "M17.11",  "50"),
        # Additional entries
        ("Aetna", "73721", "M23.61",  None),
        ("Aetna", "27130", "M16.11",  None),
        ("Aetna", "73223", "M75.1",   None),
        # Unknown combo — must return None
        ("Aetna", "99999", "Z00.00",  None),
    ]

    all_passed = True
    for payer, cpt, icd10, carc in test_cases:
        label = f"{payer} | CPT {cpt} | {icd10}" + (f" | CARC {carc}" if carc else "")
        result = get_payer_pattern(payer, cpt, icd10, carc_code=carc)
        print(f"\n── {label}")
        if result is None:
            if cpt == "99999":
                print("  → None  ✓ (expected for unknown combo)")
            else:
                print("  → None  ✗ MISSING ENTRY — check PAYER_PATTERNS keys")
                all_passed = False
        else:
            # Verify all 5 required fields are present
            required = {"denial_rate", "top_reason", "appeal_win_rate", "winning_evidence", "prevention"}
            missing = required - result.keys()
            if missing:
                print(f"  ✗ MISSING FIELDS: {missing}")
                all_passed = False
            else:
                print(json.dumps(result, indent=4))

    print("\n" + "=" * 60)
    print("RESULT:", "ALL CHECKS PASSED ✓" if all_passed else "FAILURES DETECTED ✗")
    print("=" * 60)
