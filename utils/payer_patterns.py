"""
payer_patterns — Hardcoded payer/procedure/diagnosis intelligence.

Cross-lane dependency: Person B owns this module.
This stub covers Aetna + orthopedics CPT codes used in the 3 demo scenarios.

Format: dict keyed on (payer, cpt_code, icd10_code)
Value:  denial_rate, top_reason, appeal_win_rate, winning_evidence (list[str])

Usage:
    from utils.payer_patterns import lookup_payer_pattern

    pattern = lookup_payer_pattern("Aetna", "71046", "M17.11")
    if pattern:
        print(pattern["appeal_win_rate"])   # 0.71
"""

from __future__ import annotations

from typing import TypedDict


class PayerPattern(TypedDict):
    denial_rate: float        # fraction of claims denied (0.0–1.0)
    top_reason: str           # most common denial reason
    appeal_win_rate: float    # fraction of appeals that succeed
    winning_evidence: list[str]  # evidence that correlates with successful appeals


# ---------------------------------------------------------------------------
# Pattern table — Aetna · Orthopedics · One specialty per project constraints
# ---------------------------------------------------------------------------

PAYER_PATTERNS: dict[tuple[str, str, str], PayerPattern] = {
    # Knee MRI — Medical Necessity (our primary demo scenario)
    ("Aetna", "71046", "M17.11"): {
        "denial_rate": 0.34,
        "top_reason": "Medical necessity not established — CARC 50",
        "appeal_win_rate": 0.71,
        "winning_evidence": [
            "6+ weeks documented PT (≥8 sessions with progress notes)",
            "2+ NSAID prescriptions — refill confirms adequate trial duration",
            "Orthopedic consultation note referencing failed conservative therapy",
            "Kellgren-Lawrence Grade ≥III on weight-bearing X-ray",
        ],
    },
    # Total knee replacement
    ("Aetna", "27447", "M17.11"): {
        "denial_rate": 0.18,
        "top_reason": "Medical necessity not established — CARC 50",
        "appeal_win_rate": 0.82,
        "winning_evidence": [
            "Failed 3+ months conservative management (PT + NSAIDs documented)",
            "BMI documented in pre-op note",
            "Weight-bearing X-ray within 12 months showing severe joint space narrowing",
            "Functional limitation assessment (KOOS or equivalent)",
        ],
    },
    # Partial knee replacement
    ("Aetna", "27446", "M17.11"): {
        "denial_rate": 0.21,
        "top_reason": "Medical necessity not established — CARC 50",
        "appeal_win_rate": 0.78,
        "winning_evidence": [
            "Unicompartmental disease confirmed on MRI",
            "Failed 3+ months conservative therapy",
            "BMI ≤40 documented",
            "Ligament integrity confirmed",
        ],
    },
    # Arthroscopic knee surgery
    ("Aetna", "29881", "M23.200"): {
        "denial_rate": 0.41,
        "top_reason": "Experimental/investigational — CARC 49",
        "appeal_win_rate": 0.29,
        "winning_evidence": [
            "Acute mechanical symptoms (locking, catching)",
            "MRI confirming displaced meniscal tear",
            "Failed 6-week conservative trial",
        ],
    },
    # Coding error — wrong CPT for knee MRI
    ("Aetna", "73721", "M17.11"): {
        "denial_rate": 0.12,
        "top_reason": "Claim/service lacks information — CARC 16",
        "appeal_win_rate": 0.91,
        "winning_evidence": [
            "Corrected CPT code on appeal (73721 → 71046 if MRI of hip/pelvis also ordered)",
            "Operative/procedure report confirming body part imaged",
        ],
    },
}


# ---------------------------------------------------------------------------
# Lookup helper
# ---------------------------------------------------------------------------

def lookup_payer_pattern(
    payer: str,
    cpt_code: str,
    icd10_code: str,
) -> PayerPattern | None:
    """
    Return payer intelligence for the given (payer, CPT, ICD-10) triple.

    Matching is case-insensitive on payer name and strips whitespace.
    Returns None if no pattern is found (do not raise — callers should
    degrade gracefully).

    Args:
        payer:      Payer name, e.g. "Aetna"
        cpt_code:   CPT procedure code, e.g. "71046"
        icd10_code: ICD-10-CM diagnosis code, e.g. "M17.11"

    Returns:
        PayerPattern dict or None
    """
    key = (payer.strip().title(), cpt_code.strip(), icd10_code.strip().upper())
    return PAYER_PATTERNS.get(key)


def get_all_patterns() -> dict[tuple[str, str, str], PayerPattern]:
    """Return the full pattern table. Useful for reporting."""
    return dict(PAYER_PATTERNS)
