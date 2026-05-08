# DenialGPT — Person B (Prevention + Platform)

## What This Side Builds

Person B owns the **pre-submission denial prevention** pipeline and all **platform deployment** work.

### MCP Tool
- **check_claim_policy** — Takes a claim draft (CPT + ICD-10 + payer + place of service), checks it against the Policy Knowledge Base, and returns risk flags, policy references, recommended fixes, and payer intelligence.

### Shared Utility
- **PAYER_PATTERNS** — Hardcoded dict of ~5 denial rate entries for orthopedics + Aetna combinations. Imported by both `check_claim_policy` (Person B) and `gap_analysis` (Person A).

### Infrastructure
- **Policy Knowledge Base** — ChromaDB vector store of embedded CMS LCD/NCD PDFs for orthopedics.
- **FastAPI server** — Hosts all MCP endpoints and the A2A agent card.
- **A2A Agent Card** — JSON descriptor at `/.well-known/agent.json` for Prompt Opinion discovery.
- **Railway deployment** — Public HTTPS URL for platform registration.

### Folder Structure
```
prevention/          check_claim_policy + policy_retriever
policy_kb/           ChromaDB setup, PDF embedding, retrieval
  pdfs/              CMS LCD/NCD PDF files
shared/              PAYER_PATTERNS dict (Person A imports this)
server/              FastAPI app + agent card
demo/                Demo video assets (Day 5)
```

## Tech Stack
Python, FastMCP, FastAPI, ChromaDB, LlamaIndex, OpenAI embeddings, Claude API, Railway.

## Specialty Scope
Orthopedics only. One payer: Aetna. Do not expand beyond this for the demo.
