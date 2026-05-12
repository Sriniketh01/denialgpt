# DenialGPT

**AI agent that prevents insurance claim denials before they happen — and fights them after they do.**

Built for the [Agents Assemble Hackathon](https://agents-assemble.devpost.com/) on the [Prompt Opinion](https://app.promptopinion.ai) healthcare AI platform.

---

## What It Does

Hospital billing teams lose billions of dollars annually to insurance claim denials. DenialGPT attacks the problem from both sides:

**Prevention (before submission)** — Submit a claim draft with CPT code, ICD-10 diagnosis, payer, and place of service. DenialGPT checks it against CMS LCD/NCD coverage policies and historical payer denial patterns, flags every risk before the claim leaves the building, and tells you exactly what documentation to attach.

**Post-denial analysis (after rejection)** — Paste a denial letter. DenialGPT classifies the denial type, extracts the CARC code, fetches the patient's FHIR clinical records, compares them against payer requirements, and delivers a verdict: **STRONG** appeal, **WEAK** appeal, or **DO NOT APPEAL** — with a write-off memo when it's not worth the fight.

---

## Demo Scenarios

### Scenario 1 — Prevention catch
> "Check this claim: CPT 73721, ICD-10 M17.11, Aetna, outpatient, MRI of the right knee without contrast"

DenialGPT returns **HIGH** denial risk, flags the missing prior authorization requirement, cites the CMS LCD policy section, and surfaces Aetna's 68% historical denial rate for this exact code combination with the winning evidence needed if it gets denied anyway.

### Scenario 2 — STRONG appeal (Michael Torres)
Torres was denied for knee MRI (CARC 50 — medical necessity not established). His FHIR record contains 8 PT sessions, an Ibuprofen prescription, and an orthopedic consultation note confirming conservative therapy failure. DenialGPT verdict: **STRONG** — all required evidence is documented and available.

### Scenario 3 — DO NOT APPEAL (Sarah Chen)
Same denial, different patient. Chen's FHIR chart is empty — no PT, no medications, no clinical notes. DenialGPT verdict: **DO NOT APPEAL** — generates a write-off memo so the billing team can move on instead of wasting hours on an unwinnable appeal.

---

## Architecture

```
Prompt Opinion Platform
        │
        │  A2A v1 JSON-RPC 2.0
        ▼
┌─────────────────────────────────────────────────────┐
│                   DenialGPT Server                  │
│                    (FastAPI + FastMCP)               │
│                                                     │
│  ┌──────────────────┐   ┌─────────────────────────┐ │
│  │  Prevention Path │   │   Post-Denial Pipeline  │ │
│  │                  │   │                         │ │
│  │ check_claim_     │   │ analyze_denial          │ │
│  │ policy           │   │      ↓                  │ │
│  │   ↓              │   │ fetch_clinical_evidence │ │
│  │ ChromaDB RAG     │   │   (FHIR sandbox)        │ │
│  │ (CMS LCD/NCD)    │   │      ↓                  │ │
│  │   +              │   │ gap_analysis            │ │
│  │ PAYER_PATTERNS   │   │   + PAYER_PATTERNS      │ │
│  └──────────────────┘   └─────────────────────────┘ │
└─────────────────────────────────────────────────────┘
        │
        │  Claude Sonnet (Anthropic API)
        │  Voyage AI embeddings
        │  ChromaDB vector store
```

**Routing logic:** Incoming messages containing a CPT code + ICD-10 pattern (without denial keywords) are routed to the prevention path. Denial letters route to the post-denial pipeline.

---

## Tools

| Tool | Description |
|------|-------------|
| `check_claim_policy` | RAG-based pre-submission risk check against CMS LCD/NCD policies + payer intelligence |
| `analyze_denial` | Classifies denial type, extracts CARC code, identifies root cause |
| `fetch_clinical_evidence` | Queries FHIR sandbox for patient records relevant to the denial type |
| `gap_analysis` | Compares payer requirements vs FHIR evidence, returns STRONG / WEAK / DO NOT APPEAL |

---

## Policy Knowledge Base

CMS LCD/NCD policy documents for orthopedics, embedded into ChromaDB using Voyage AI (`voyage-3`). Powers the RAG retrieval in `check_claim_policy`.

| Document | Coverage Area |
|----------|--------------|
| LCD L36575 — Total Knee Arthroplasty | TKA coverage criteria |
| LCD L36007 — Lower Extremity Major Joint Replacement | Hip and knee replacement |
| LCD L34220 — Lumbar MRI | MRI medical necessity |
| LCD L38484 — MRI Extremities | Extremity MRI coverage |
| LCD L36690 — Arthroplasty Knee/Hip | Arthroplasty policy |
| LCD L34938 — Physical Therapy | Conservative therapy requirements |

---

## Payer Intelligence (PAYER_PATTERNS)

Hardcoded denial pattern data for Aetna + orthopedics combinations. Used by both `check_claim_policy` (prevention) and `gap_analysis` (post-denial).

| CPT | ICD-10 | Denial Rate | Top Reason | Appeal Win Rate |
|-----|--------|-------------|------------|-----------------|
| 73721 | M17.11 | 68% | Prior auth not obtained | 41% |
| 27447 | M17.11 | 45% | Medical necessity | 58% |
| 73721 | M23.61 | 52% | Conservative therapy not documented | 47% |
| 27130 | M16.11 | 38% | Medical necessity | 62% |
| 73223 | M75.1  | 61% | Prior auth not obtained | 44% |

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Agent framework | Google ADK + A2A v1 protocol |
| API server | FastAPI + FastMCP |
| LLM | Claude Sonnet (Anthropic API) |
| Embeddings | Voyage AI `voyage-3` |
| Vector store | ChromaDB (persistent) |
| PDF chunking | LlamaIndex SentenceSplitter |
| FHIR client | Prompt Opinion FHIR sandbox |
| Deployment | Render |

---

## Repository Structure

```
main.py                        FastAPI server + A2A endpoint + routing logic
prevention/
  check_claim_policy.py        Pre-submission risk check tool (RAG + payer intelligence)
policy_kb/
  embed_pdfs.py                Chunk and embed CMS policy documents into ChromaDB
  retrieve.py                  Semantic search over the policy knowledge base
  pdfs/                        CMS LCD/NCD .txt policy documents
shared/
  payer_patterns.py            PAYER_PATTERNS dict 
tools/
  analyze_denial.py            Post-denial classification tool
  fetch_evidence.py            FHIR clinical evidence fetcher
  gap_analysis.py              Appeal viability verdict tool
prompts/
  analyze_denial.txt           System prompt for denial classification
  gap_analysis.txt             System prompt for gap analysis
middleware/
  sharp.py                     SHARP header extraction (FHIR context)
tests/
  test_e2e_promptopinion.py    End-to-end tests against live FHIR sandbox
render.yaml                    Render deployment config
requirements.txt               Python dependencies
```

---

## Running Locally

```bash
# 1. Clone and install
git clone https://github.com/Sriniketh01/denialgpt.git
cd denialgpt
pip install -r requirements.txt

# 2. Set environment variables
cp .env.example .env
# Fill in: ANTHROPIC_API_KEY, VOYAGE_API_KEY, FHIR_BASE_URL, DEV_ACCESS_TOKEN

# 3. Build the policy knowledge base
python -m policy_kb.embed_pdfs

# 4. Start the server
uvicorn main:app --reload --port 8000

# 5. Check it's running
curl http://localhost:8000/health
curl http://localhost:8000/.well-known/agent-card.json
```

---

## Deployment

Deployed on Render. The `startCommand` in `render.yaml` runs the ChromaDB embed pipeline before starting uvicorn, so the policy knowledge base is built fresh on every deploy.

Required environment variables on Render:
- `ANTHROPIC_API_KEY`
- `VOYAGE_API_KEY`
- `FHIR_BASE_URL`
- `DEV_ACCESS_TOKEN`

---

## Team

Built for the **Agents Assemble Hackathon** on Devpost, hosted on the Prompt Opinion healthcare AI platform.

- **Sriniketh** — Post-denial pipeline: `analyze_denial`, `fetch_clinical_evidence`, `gap_analysis`, FHIR integration, server architecture
- **Harshitha** — Prevention pipeline: `check_claim_policy`, Policy Knowledge Base, PAYER_PATTERNS, platform deployment
