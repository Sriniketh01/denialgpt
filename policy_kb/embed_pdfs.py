"""
embed_pdfs — Chunk and embed CMS LCD/NCD PDFs into ChromaDB.

Pipeline:
    1. Read all PDFs from policy_kb/pdfs/
    2. Chunk using LlamaIndex SentenceSplitter (512 tokens, 50 overlap)
    3. Generate embeddings via Voyage AI voyage-3-lite
    4. Store in ChromaDB collection "policy_kb" (persisted to policy_kb/chroma_store/)

Usage:
    python -m policy_kb.embed_pdfs

Requires VOYAGE_API_KEY in .env file at project root.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from llama_index.core import SimpleDirectoryReader
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.voyageai import VoyageEmbedding

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CHUNK_SIZE: int = 512
CHUNK_OVERLAP: int = 50
EMBEDDING_MODEL: str = "voyage-3-lite"
COLLECTION_NAME: str = "policy_kb"
EMBED_BATCH_SIZE: int = 64

# Paths (relative to project root)
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
PDF_DIR: Path = PROJECT_ROOT / "policy_kb" / "pdfs"
CHROMA_DIR: Path = PROJECT_ROOT / "policy_kb" / "chroma_store"


def get_already_embedded(collection: chromadb.Collection) -> set[str]:
    """Return the set of source filenames already stored in the collection."""
    if collection.count() == 0:
        return set()
    results = collection.get(include=["metadatas"])
    sources: set[str] = set()
    for meta in results["metadatas"] or []:
        if meta and "source_file" in meta:
            sources.add(meta["source_file"])
    return sources


def load_and_chunk_pdfs(
    pdf_dir: Path,
    skip_files: set[str],
) -> tuple[list[str], list[str], list[dict], list[str]]:
    """Load PDFs, chunk them, and return parallel lists for ChromaDB.

    Returns:
        (ids, texts, metadatas, processed_filenames)
    """
    pdf_paths: list[Path] = sorted(
        p for p in pdf_dir.iterdir()
        if p.suffix.lower() == ".pdf"
    )

    if not pdf_paths:
        print("  No PDF files found in", pdf_dir)
        return [], [], [], []

    ids: list[str] = []
    texts: list[str] = []
    metadatas: list[dict] = []
    processed: list[str] = []

    splitter = SentenceSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )

    for pdf_path in pdf_paths:
        filename = pdf_path.name
        if filename in skip_files:
            print(f"  SKIP (already embedded): {filename}")
            continue

        print(f"  Processing: {filename}")
        try:
            docs = SimpleDirectoryReader(
                input_files=[str(pdf_path)],
            ).load_data()
        except Exception as exc:
            print(f"    ERROR reading {filename}: {exc}")
            continue

        nodes = splitter.get_nodes_from_documents(docs)
        print(f"    Loaded {len(docs)} page(s) → {len(nodes)} chunk(s)")

        for i, node in enumerate(nodes):
            chunk_id = f"{filename}::chunk_{i:04d}"
            ids.append(chunk_id)
            texts.append(node.get_content())
            metadatas.append({
                "source_file": filename,
                "chunk_index": i,
                "total_chunks": len(nodes),
            })

        processed.append(filename)

    return ids, texts, metadatas, processed


def embed_texts(
    texts: list[str],
    api_key: str,
) -> list[list[float]]:
    """Generate embeddings for a list of texts using Voyage AI."""
    embed_model = VoyageEmbedding(
        model_name=EMBEDDING_MODEL,
        voyage_api_key=api_key,
        embed_batch_size=EMBED_BATCH_SIZE,
    )
    print(f"  Generating embeddings for {len(texts)} chunk(s) ...")
    embeddings: list[list[float]] = embed_model.get_text_embedding_batch(texts)
    return embeddings


def main() -> None:
    """Run the full embed pipeline."""
    # ------------------------------------------------------------------
    # 1. Load env
    # ------------------------------------------------------------------
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("VOYAGE_API_KEY", "")
    if not api_key:
        print("ERROR: VOYAGE_API_KEY not set. Add it to .env at project root.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 2. Init ChromaDB
    # ------------------------------------------------------------------
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_or_create_collection(name=COLLECTION_NAME)
    print(f"ChromaDB collection '{COLLECTION_NAME}' — {collection.count()} existing chunks")

    already_embedded = get_already_embedded(collection)
    if already_embedded:
        print(f"  Already embedded: {', '.join(sorted(already_embedded))}")

    # ------------------------------------------------------------------
    # 3. Load + chunk PDFs
    # ------------------------------------------------------------------
    print("\nChunking PDFs ...")
    ids, texts, metadatas, processed = load_and_chunk_pdfs(PDF_DIR, already_embedded)

    if not ids:
        print("\nNothing new to embed. Done.")
        return

    # ------------------------------------------------------------------
    # 4. Generate embeddings
    # ------------------------------------------------------------------
    print("\nEmbedding ...")
    embeddings = embed_texts(texts, api_key)

    # ------------------------------------------------------------------
    # 5. Store in ChromaDB
    # ------------------------------------------------------------------
    print("\nStoring in ChromaDB ...")
    collection.add(
        ids=ids,
        documents=texts,
        embeddings=embeddings,
        metadatas=metadatas,
    )

    # ------------------------------------------------------------------
    # 6. Summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 50)
    print("EMBEDDING COMPLETE")
    print(f"  Documents embedded: {len(processed)}")
    for name in processed:
        print(f"    - {name}")
    print(f"  Chunks created:     {len(ids)}")
    print(f"  Total in collection: {collection.count()}")
    print("=" * 50)


if __name__ == "__main__":
    main()
