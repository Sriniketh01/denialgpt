"""
check_claim_policy — MCP Tool (Person B)

Takes a claim draft (CPT + ICD-10 + payer + place of service), checks it against
the Policy Knowledge Base (ChromaDB), and returns risk flags, policy references,
recommended fixes, and a payer_intelligence block from PAYER_PATTERNS.

Inputs:
    - cpt_codes: list of CPT codes (e.g., ["71046"])
    - icd10_codes: list of ICD-10 codes (e.g., ["M17.11"])
    - payer_name: str (e.g., "Aetna")
    - place_of_service: str (e.g., "Outpatient")
    - procedure_description: str (optional)

Outputs:
    - risk_flags: list of identified risks
    - policy_references: list of cited policy sources
    - overall_risk: LOW | MEDIUM | HIGH
    - recommended_fixes: list of actions to take before submitting
    - payer_intelligence: block from PAYER_PATTERNS (denial_rate, top_reason, etc.)

Implementation: Day 2
"""

# TODO: Day 2 — implement RAG retrieval + risk scoring + payer_intelligence injection
