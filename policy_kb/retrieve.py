"""
retrieve — ChromaDB Policy Knowledge Base Query Module

Provides async semantic search over the embedded CMS LCD/NCD policy documents.
Given a CPT code, ICD-10 code, and payer name, retrieves the most relevant
policy chunks from ChromaDB using Voyage AI embeddings.

Collection:  policy_kb (persisted at policy_kb/chroma_store/)
Embeddings:  Voyage AI voyage-3 (must match the model used in embed_pdfs.py)
Auth:        VOYAGE_API_KEY from .env

Typical usage
-------------
    from policy_kb.retrieve import retrieve_policy_chunks

    chunks = await retrieve_policy_chunks(
        cpt_code="71046",
        icd10_code="M17.11",
        payer="Aetna",
        top_k=5,
    )
    for chunk in chunks:
        print(chunk["score"], chunk["source"])
        print(chunk["text"][:200])
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

import chromadb
import voyageai
from dotenv import load_dotenv

if TYPE_CHECKING:
    pass  # keep runtime imports minimal

# ---------------------------------------------------------------------------
# Config — must match embed_pdfs.py to ensure vector-space consistency
# ---------------------------------------------------------------------------
EMBEDDING_MODEL: str = "voyage-3"
COLLECTION_NAME: str = "policy_kb"

# Paths (resolved relative to this file so imports work from any cwd)
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
CHROMA_DIR: Path = PROJECT_ROOT / "policy_kb" / "chroma_store"
ENV_PATH: Path = PROJECT_ROOT / ".env"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def retrieve_policy_chunks(
    cpt_code: str,
    icd10_code: str,
    payer: str,
    procedure_description: str = "",
    top_k: int = 5,
) -> list[dict]:
    """Retrieve the most relevant CMS policy chunks for a claim combination.

    Builds a rich natural-language query from the claim inputs, embeds it with
    Voyage AI, and performs semantic search against the persisted ChromaDB
    collection.  Chunks scoring below MIN_SCORE are filtered out unless all
    chunks fall below that threshold (low-quality retrieval beats no retrieval).

    Parameters
    ----------
    cpt_code : str
        Procedure code, e.g. ``"73721"`` (knee MRI).
    icd10_code : str
        Diagnosis code, e.g. ``"M17.11"`` (osteoarthritis right knee).
    payer : str
        Insurance payer name, e.g. ``"Aetna"``.
    procedure_description : str
        Human-readable procedure name, e.g. ``"MRI of the right knee without
        contrast"``. Included in the query to improve semantic relevance.
        Defaults to empty string (omitted from query if blank).
    top_k : int
        Number of most-relevant chunks to return (default 5).

    Returns
    -------
    list[dict]
        Each dict contains:
        - ``"text"``   — the policy chunk content
        - ``"source"`` — the PDF filename the chunk came from
        - ``"score"``  — cosine similarity score (higher = more relevant, 0–1)

    Raises
    ------
    EnvironmentError
        If ``VOYAGE_API_KEY`` is not set in the environment.
    FileNotFoundError
        If the ChromaDB store at ``policy_kb/chroma_store/`` does not exist.
        Run ``python -m policy_kb.embed_pdfs`` first.
    ValueError
        If the collection is empty (no documents have been embedded yet).
    """
    load_dotenv(ENV_PATH)

    api_key = os.getenv("VOYAGE_API_KEY", "")
    if not api_key:
        raise EnvironmentError(
            "VOYAGE_API_KEY is not set. Add it to .env at project root."
        )

    if not CHROMA_DIR.exists():
        raise FileNotFoundError(
            f"ChromaDB store not found at {CHROMA_DIR}. "
            "Run `python -m policy_kb.embed_pdfs` first to embed the policy PDFs."
        )

    # ------------------------------------------------------------------
    # 1. Connect to persisted ChromaDB
    # ------------------------------------------------------------------
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_or_create_collection(name=COLLECTION_NAME)

    if collection.count() == 0:
        raise ValueError(
            f"ChromaDB collection '{COLLECTION_NAME}' is empty. "
            "Run `python -m policy_kb.embed_pdfs` first."
        )

    # ------------------------------------------------------------------
    # 2. Build natural-language query
    # ------------------------------------------------------------------
    proc = f" procedure {procedure_description}" if procedure_description.strip() else ""
    query_text = (
        f"Medicare coverage policy for CPT code {cpt_code}{proc} "
        f"with diagnosis {icd10_code} for payer {payer}. "
        f"Prior authorization requirements, medical necessity criteria, "
        f"conservative therapy documentation, coverage limitations and denial risk factors."
    )

    # ------------------------------------------------------------------
    # 3. Embed the query using Voyage AI (runs in thread pool — non-blocking)
    # ------------------------------------------------------------------
    query_embedding = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: _embed_query(query_text, api_key),
    )

    # ------------------------------------------------------------------
    # 4. Query ChromaDB for top_k most relevant chunks
    # ------------------------------------------------------------------
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    # ------------------------------------------------------------------
    # 5. Format results
    # ------------------------------------------------------------------
    chunks: list[dict] = []

    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    for text, meta, distance in zip(documents, metadatas, distances):
        # ChromaDB returns distance (L2 or cosine depending on collection config).
        # 1 / (1 + distance) always yields a value in (0, 1] with higher → more relevant,
        # and is safe for both L2 and cosine distance metrics.
        score = round(float(1.0 / (1.0 + distance)), 4)
        source = (meta or {}).get("source_file", "unknown")
        chunks.append(
            {
                "text": text,
                "source": source,
                "score": score,
            }
        )

    # ------------------------------------------------------------------
    # 6. Minimum score filter
    # ------------------------------------------------------------------
    # Remove chunks below the relevance threshold.  If ALL chunks fall
    # below it, return them all anyway — low-quality retrieval is better
    # than returning nothing, since the LLM can still reason over weak
    # signal and payer_intelligence fills any remaining gaps.
    MIN_SCORE: float = 0.45
    filtered = [c for c in chunks if c["score"] >= MIN_SCORE]
    return filtered if filtered else chunks


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _embed_query(text: str, api_key: str) -> list[float]:
    """Synchronous helper: embed a single query string with Voyage AI.

    Uses ``input_type="query"`` so Voyage applies query-optimised encoding,
    which pairs correctly with ``input_type="document"`` embeddings stored
    in ChromaDB.

    Parameters
    ----------
    text : str
        The natural-language query to embed.
    api_key : str
        Voyage AI API key.

    Returns
    -------
    list[float]
        The embedding vector.
    """
    vo = voyageai.Client(api_key=api_key)
    result = vo.embed(
        texts=[text],
        model=EMBEDDING_MODEL,
        input_type="query",
    )
    return result.embeddings[0]


# ---------------------------------------------------------------------------
# __main__ — quick smoke-test for the demo scenario
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    async def _smoke_test() -> None:
        print("=" * 60)
        print("RETRIEVE SMOKE TEST")
        print(f"  model:       {EMBEDDING_MODEL}")
        print(f"  collection:  {COLLECTION_NAME}")
        print(f"  chroma_dir:  {CHROMA_DIR}")
        print("=" * 60)

        test_cpt = "73721"
        test_icd10 = "M17.11"
        test_payer = "Aetna"
        test_proc = "MRI of the right knee without contrast"

        print(f"\nQuery: CPT={test_cpt}  ICD-10={test_icd10}  "
              f"Payer={test_payer}  Procedure={test_proc}\n")

        try:
            chunks = await retrieve_policy_chunks(
                cpt_code=test_cpt,
                icd10_code=test_icd10,
                payer=test_payer,
                procedure_description=test_proc,
                top_k=5,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"[ERROR] {exc}")
            print("\nRun `python -m policy_kb.embed_pdfs` first, then retry.")
            return

        if not chunks:
            print("[WARN] No chunks returned — check collection contents.")
            return

        print(f"Returned {len(chunks)} chunk(s):\n")
        for i, chunk in enumerate(chunks, 1):
            print(f"── Chunk {i} ─────────────────────────────────────")
            print(f"  source : {chunk['source']}")
            print(f"  score  : {chunk['score']}")
            print(f"  text   : {chunk['text'][:300].strip()}")
            if len(chunk["text"]) > 300:
                print("           [... truncated ...]")
            print()

        print("=" * 60)
        print("Full JSON of chunk 1:")
        print(json.dumps(chunks[0], indent=2))

    asyncio.run(_smoke_test())
