"""
embed_pdfs — Script to chunk and embed CMS LCD/NCD PDFs into ChromaDB

Pipeline:
    1. Read PDFs from policy_kb/pdfs/
    2. Chunk using LlamaIndex (or manual splitter) with overlap
    3. Generate embeddings via OpenAI text-embedding-3-small
    4. Store in ChromaDB collection

Usage:
    python -m policy_kb.embed_pdfs

Implementation: Day 1 (end of day)
"""

# TODO: Day 1 — implement PDF chunking + embedding pipeline
