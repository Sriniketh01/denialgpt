"""
policy_retriever — RAG retrieval layer for check_claim_policy

Queries the ChromaDB Policy Knowledge Base with CPT + ICD-10 + payer context
and returns the most relevant policy document chunks for LLM reasoning.

Uses the retrieve() function from policy_kb/retrieve.py under the hood.

Implementation: Day 2
"""

# TODO: Day 2 — implement retrieval wrapper that formats chunks for the LLM prompt
