"""
embed_pdfs — Chunk and embed CMS LCD/NCD policy documents into ChromaDB.

Pipeline:
    1. Read all PDFs from policy_kb/pdfs/ using PyMuPDF (fitz) for clean
       per-page text extraction — avoids the boilerplate header/footer noise
       produced by generic PDF-to-text converters.
    2. Chunk using LlamaIndex SentenceSplitter (512 tokens, 50 overlap)
    3. Filter out boilerplate chunks (revision history, copyright notices, etc.)
    4. Generate embeddings via Voyage AI voyage-3
    5. Store in ChromaDB collection "policy_kb" with cosine distance metric
       (persisted to policy_kb/chroma_store/)

Usage:
    python -m policy_kb.embed_pdfs           # incremental — skips already-embedded files
    python -m policy_kb.embed_pdfs --reset   # wipe collection and re-embed everything

Requires VOYAGE_API_KEY in .env file at project root.

Notes:
    - Collection uses cosine distance (hnsw:space = cosine) so retrieve.py
      scores are in (0, 1] with higher = more relevant.
    - PyMuPDF extracts text page-by-page; pages are joined with double newlines
      before chunking so sentence boundaries are respected across pages.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import chromadb
import fitz  # PyMuPDF
from dotenv import load_dotenv
from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.voyageai import VoyageEmbedding

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CHUNK_SIZE: int = 512
CHUNK_OVERLAP: int = 50
EMBEDDING_MODEL: str = "voyage-3"
COLLECTION_NAME: str = "policy_kb"
EMBED_BATCH_SIZE: int = 64

# Minimum character length for a chunk to be embedded.
# Filters out very short fragments (standalone page numbers, single lines, etc.)
MIN_CHUNK_CHARS: int = 150

# Boilerplate substrings — chunks containing ANY of these are skipped (no size guard).
# These patterns identify administrative/legal content with zero clinical signal.
BOILERPLATE_PATTERNS: tuple[str, ...] = (
    # AHA notices (present in every LCD)
    "views and/or positions presented in the material do not",
    "not endorsed by the AHA",
    "AHA copyrighted materials",
    "ub04@aha.org",
    "UB-04 Manual",
    "If an entity wishes to utilize",
    # CMS web artifacts from browser-printed PDFs
    "https://www.cms.gov",
    "http://www.cms.gov",
    "medicare-coverage-database",
    # Revision history table markers
    "Revision History Explanation",
    "Reasons for\nChange",
    "Reasons for Change",
    # Verbatim boilerplate sentence in every revision history entry
    "21st Century Cures Act will apply to new and revised LCDs that restrict coverage which requires comment and notice. This revision is not a restriction",
    # CPT copyright block
    "CPT codes, descriptions, and other data only are copyright",
    "Fee schedules, relative value units, conversion factors",
)

# Paths
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
PDF_DIR: Path = PROJECT_ROOT / "policy_kb" / "pdfs"
CHROMA_DIR: Path = PROJECT_ROOT / "policy_kb" / "chroma_store"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def is_boilerplate(text: str) -> bool:
    """Return True if the chunk should be excluded from the collection."""
    stripped = text.strip()
    if len(stripped) < MIN_CHUNK_CHARS:
        return True
    # No size guard — boilerplate patterns are specific enough that a match
    # in any chunk signals administrative noise, not clinical content.
    low = stripped.lower()
    for pattern in BOILERPLATE_PATTERNS:
        if pattern.lower() in low:
            return True
    return False


# Line-level patterns to strip from raw PDF text before chunking.
# These are browser-print artifacts that appear as standalone lines on every page.
_STRIP_LINE_PATTERNS: tuple[str, ...] = (
    # Browser print header: timestamp
    "am\nlcd -",
    "pm\nlcd -",
    # Inline patterns matched per line:
    "views and/or positions presented in the material do not",
    "not endorsed by the aha",
    "cpt codes, descriptions, and other data only are copyright",
    "fee schedules, relative value units, conversion factors",
    "https://www.cms.gov",
    "http://www.cms.gov",
    "medicare-coverage-database",
)


def clean_raw_text(text: str) -> str:
    """Strip browser-print artifacts from raw PDF text before chunking.

    Removes lines that are purely navigational or legal boilerplate:
    - Timestamps (e.g. "5/7/26, 8:52 PM")
    - Repeated page titles (e.g. "LCD - Total Knee Arthroplasty (L36575)")
    - CMS URLs
    - Page-number markers (e.g. "5/21")
    - Copyright / AHA disclaimer lines

    Substantive clinical sentences that happen to span a line boundary and
    contain one of these strings are preserved — this function is line-level,
    not substring-level.
    """
    import re

    cleaned_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()

        # Skip empty lines (will be re-added as paragraph breaks below)
        if not stripped:
            cleaned_lines.append("")
            continue

        low = stripped.lower()

        # Timestamp lines: match "M/D/YY, H:MM AM" or "M/D/YY, H:MM PM"
        if re.match(r"\d{1,2}/\d{1,2}/\d{2,4},\s+\d{1,2}:\d{2}\s+(am|pm)", low):
            continue

        # Page-number markers: "5/21", "12/21", etc.
        if re.match(r"^\d{1,3}/\d{1,3}$", stripped):
            continue

        # Repeated page-title lines: "LCD - <anything> (L#####)"
        if re.match(r"^lcd\s+-\s+.+\(l\d+\)", low):
            continue

        # Lines that are purely one of the strip patterns
        skip = False
        for pattern in _STRIP_LINE_PATTERNS:
            if pattern in low:
                skip = True
                break
        if skip:
            continue

        cleaned_lines.append(line)

    # Collapse runs of 3+ blank lines into 2 (preserve paragraph structure)
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned_lines))
    return result.strip()


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract clean full text from a PDF using PyMuPDF.

    Joins all pages with double newlines to preserve paragraph boundaries.
    """
    doc = fitz.open(str(pdf_path))
    pages: list[str] = []
    for page in doc:
        page_text = page.get_text("text")  # plain text, no layout artifacts
        if page_text.strip():
            pages.append(page_text.strip())
    doc.close()
    return "\n\n".join(pages)


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
    """Extract text from PDFs and .txt files, chunk, filter boilerplate.

    Processes both file types from the same directory:
    - .pdf  — extracted via PyMuPDF (fitz), then cleaned
    - .txt  — read with plain open().read(), no cleaning needed

    .txt files that are placeholder stubs (< 150 chars) are skipped silently.

    Returns:
        (ids, texts, metadatas, processed_filenames)
    """
    all_paths: list[Path] = sorted(
        p for p in pdf_dir.iterdir()
        if p.suffix.lower() in {".pdf", ".txt"}
    )

    if not all_paths:
        print("  No .pdf or .txt files found in", pdf_dir)
        return [], [], [], []

    ids: list[str] = []
    texts: list[str] = []
    metadatas: list[dict] = []
    processed: list[str] = []

    splitter = SentenceSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )

    for doc_path in all_paths:
        filename = doc_path.name
        if filename in skip_files:
            print(f"  SKIP (already embedded): {filename}")
            continue

        print(f"  Processing: {filename}")

        # -- Extract raw text depending on file type --
        if doc_path.suffix.lower() == ".pdf":
            try:
                full_text = extract_text_from_pdf(doc_path)
            except Exception as exc:
                print(f"    ERROR reading {filename}: {exc}")
                continue
            if not full_text.strip():
                print(f"    WARN: no text extracted from {filename} — skipping")
                continue
            # Strip browser-print artifacts from PDFs before chunking
            full_text = clean_raw_text(full_text)
        else:
            # .txt — plain read, no PDF parser needed
            try:
                full_text = doc_path.read_text(encoding="utf-8")
            except Exception as exc:
                print(f"    ERROR reading {filename}: {exc}")
                continue
            if len(full_text.strip()) < MIN_CHUNK_CHARS:
                print(f"    SKIP (placeholder/empty): {filename}")
                continue

        # Wrap in a LlamaIndex Document so SentenceSplitter can chunk it
        doc = Document(text=full_text, metadata={"source_file": filename})
        nodes = splitter.get_nodes_from_documents([doc])
        raw_count = len(nodes)

        chunk_index = 0
        for node in nodes:
            content = node.get_content()
            if is_boilerplate(content):
                continue
            chunk_id = f"{filename}::chunk_{chunk_index:04d}"
            ids.append(chunk_id)
            texts.append(content)
            metadatas.append({
                "source_file": filename,
                "chunk_index": chunk_index,
            })
            chunk_index += 1

        kept = chunk_index
        skipped = raw_count - kept
        print(f"    {raw_count} raw chunks → {kept} kept, {skipped} boilerplate filtered")
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
    reset = "--reset" in sys.argv

    # ------------------------------------------------------------------
    # 1. Load env
    # ------------------------------------------------------------------
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("VOYAGE_API_KEY", "")
    if not api_key:
        print("ERROR: VOYAGE_API_KEY not set. Add it to .env at project root.")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 2. Init ChromaDB — cosine distance so retrieve.py scores are in (0, 1]
    # ------------------------------------------------------------------
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    if reset:
        print(f"--reset: deleting existing collection '{COLLECTION_NAME}' ...")
        try:
            client.delete_collection(name=COLLECTION_NAME)
        except Exception:
            pass  # collection didn't exist yet

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},  # cosine distance → scores in (0, 1]
    )
    print(f"ChromaDB collection '{COLLECTION_NAME}' — {collection.count()} existing chunks")

    already_embedded = get_already_embedded(collection)
    if already_embedded:
        print(f"  Already embedded: {', '.join(sorted(already_embedded))}")

    # ------------------------------------------------------------------
    # 3. Load + chunk PDFs
    # ------------------------------------------------------------------
    print("\nChunking policy documents ...")
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
    print(f"  Chunks stored:       {len(ids)}")
    print(f"  Total in collection: {collection.count()}")
    print("=" * 50)


if __name__ == "__main__":
    main()
