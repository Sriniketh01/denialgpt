"""
PAYER_PATTERNS — Shared utility (Person B builds, Person A imports)

Hardcoded Python dict of known denial patterns for specific
(payer, CPT, ICD-10) combinations. Scoped to orthopedics + Aetna for MVP.

Used by:
    - check_claim_policy (Person B) — injects payer_intelligence into prevention output
    - gap_analysis (Person A) — injects appeal win rate + winning evidence into reasoning

Keys:   (payer, cpt_code, icd10_code)
Values: {
    denial_rate: float,       # e.g., 0.68
    top_reason: str,          # e.g., "Prior auth not obtained"
    appeal_win_rate: float,   # e.g., 0.41
    winning_evidence: list,   # e.g., ["PT notes", "NSAID failure statement"]
}

Final version: Day 2 end-of-day (pushed to shared repo for Person A)
"""

# TODO: Day 2 — populate with ~5 orthopedics + Aetna entries
PAYER_PATTERNS: dict = {}
