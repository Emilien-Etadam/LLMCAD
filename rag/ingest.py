"""Ingest Build123d documentation into Qdrant using TEI (BGE-M3) embeddings."""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

import requests
import tiktoken
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

QDRANT_URL = os.getenv("QDRANT_URL", "http://192.168.30.127:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "build123d_docs")
TEI_URL = os.getenv("TEI_URL", "http://192.168.30.121:8080")
DOCS_PATH = os.getenv(
    "BUILD123D_DOCS_PATH", os.path.expanduser("~/build123d-source/docs")
)
CHUNK_TOKENS = int(os.getenv("CHUNK_TOKENS", "500"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "8"))
VECTOR_SIZE = 1024
QDRANT_UPSERT_BATCH = 64
PROGRESS_LOG_INTERVAL_SEC = 1.0


def check_services() -> None:
    """Verify Qdrant, TEI, and docs directory before any writes."""
    q = requests.get(f"{QDRANT_URL.rstrip('/')}/healthz", timeout=30)
    if q.status_code != 200:
        print(
            f"[ingest] ERROR: Qdrant not healthy ({QDRANT_URL}/healthz "
            f"returned HTTP {q.status_code}). Aborting; no collection changes.",
            file=sys.stderr,
        )
        sys.exit(1)
    t = requests.get(f"{TEI_URL.rstrip('/')}/health", timeout=30)
    if t.status_code != 200:
        print(
            f"[ingest] ERROR: TEI not healthy ({TEI_URL}/health "
            f"returned HTTP {t.status_code}). Aborting; no collection changes.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not os.path.isdir(DOCS_PATH):
        print(
            f"[ingest] ERROR: docs path does not exist or is not a directory: "
            f"{DOCS_PATH!r}. Aborting; no collection changes.",
            file=sys.stderr,
        )
        sys.exit(1)


def recreate_collection(client: QdrantClient) -> None:
    """Drop collection if present and recreate with 1024-dim cosine vectors."""
    if client.collection_exists(QDRANT_COLLECTION):
        client.delete_collection(collection_name=QDRANT_COLLECTION)
    client.create_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
    )
    print(
        f"[ingest] Collection '{QDRANT_COLLECTION}' (re)created with "
        f"{VECTOR_SIZE} dims, COSINE"
    )


def discover_files(docs_path: str) -> list[Path]:
    """Return sorted unique paths for all *.rst and *.py under docs_path (recursive)."""
    root = Path(docs_path)
    rst_files = list(root.rglob("*.rst"))
    py_files = list(root.rglob("*.py"))
    combined = sorted({p.resolve() for p in rst_files + py_files}, key=lambda p: str(p))
    return combined


def chunk_file(
    path: Path,
    encoder: tiktoken.Encoding,
    chunk_tokens: int,
    overlap: int,
) -> list[dict[str, object]]:
    """Token-chunk file content; decode each slice back to text for embedding."""
    stride = chunk_tokens - overlap
    if stride <= 0:
        print(
            "[ingest] ERROR: CHUNK_OVERLAP must be less than CHUNK_TOKENS.",
            file=sys.stderr,
        )
        sys.exit(1)

    raw = path.read_text(encoding="utf-8", errors="replace")
    tokens = encoder.encode(raw)
    suffix = path.suffix.lower()
    file_type = "rst" if suffix == ".rst" else "py"

    chunks: list[dict[str, object]] = []
    if not tokens:
        return chunks

    start = 0
    chunk_index = 0
    while start < len(tokens):
        end = min(start + chunk_tokens, len(tokens))
        slice_tokens = tokens[start:end]
        chunk_text = encoder.decode(slice_tokens)
        chunks.append(
            {
                "source_file": path.name,
                "file_type": file_type,
                "chunk_index": chunk_index,
                "token_count": len(slice_tokens),
                "text": chunk_text,
            }
        )
        chunk_index += 1
        if end >= len(tokens):
            break
        start += stride

    return chunks


def _normalize_embed_response(data: object) -> list[list[float]]:
    """Parse TEI /embed JSON into a list of embedding vectors."""
    if isinstance(data, list):
        if data and isinstance(data[0], (int, float)):
            return [list(map(float, data))]  # single vector flat list
        out: list[list[float]] = []
        for row in data:
            if not isinstance(row, list):
                continue
            out.append([float(x) for x in row])
        return out
    if isinstance(data, dict):
        if "embeddings" in data and isinstance(data["embeddings"], list):
            return _normalize_embed_response(data["embeddings"])
        if "data" in data and isinstance(data["data"], list):
            vecs: list[list[float]] = []
            for item in data["data"]:
                if isinstance(item, dict) and "embedding" in item:
                    emb = item["embedding"]
                    if isinstance(emb, list):
                        vecs.append([float(x) for x in emb])
            if vecs:
                return vecs
    print(f"[ingest] ERROR: Unexpected TEI embed response shape: {type(data)}", file=sys.stderr)
    sys.exit(1)


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Request embeddings for a batch of texts from TEI."""
    url = f"{TEI_URL.rstrip('/')}/embed"
    r = requests.post(url, json={"inputs": texts}, timeout=300)
    if r.status_code != 200:
        print(
            f"[ingest] ERROR: TEI embed failed: HTTP {r.status_code} {r.text}",
            file=sys.stderr,
        )
        sys.exit(1)
    vectors = _normalize_embed_response(r.json())
    if len(vectors) != len(texts):
        print(
            f"[ingest] ERROR: TEI returned {len(vectors)} vectors for {len(texts)} inputs.",
            file=sys.stderr,
        )
        sys.exit(1)
    for i, v in enumerate(vectors):
        if len(v) != VECTOR_SIZE:
            print(
                f"[ingest] ERROR: embedding {i} has dim {len(v)}, expected {VECTOR_SIZE}.",
                file=sys.stderr,
            )
            sys.exit(1)
    return vectors


def upsert_chunks(
    client: QdrantClient, chunks_with_vectors: list[tuple[dict[str, object], list[float]]]
) -> None:
    """Upsert a batch of points with metadata payload and vectors."""
    points: list[PointStruct] = []
    for meta, vector in chunks_with_vectors:
        pid = uuid.uuid4().hex
        payload = dict(meta)
        points.append(PointStruct(id=pid, vector=vector, payload=payload))
    client.upsert(collection_name=QDRANT_COLLECTION, points=points)


def main() -> None:
    t0 = time.perf_counter()
    check_services()

    client = QdrantClient(url=QDRANT_URL.rstrip("/"))
    recreate_collection(client)

    files = discover_files(DOCS_PATH)
    print(f"[ingest] Found {len(files)} files under {DOCS_PATH}")

    encoder = tiktoken.get_encoding("cl100k_base")
    pending: list[tuple[dict[str, object], list[float]]] = []
    total_points = 0

    last_progress_log = 0.0

    for idx, path in enumerate(files, start=1):
        chunks = chunk_file(path, encoder, CHUNK_TOKENS, CHUNK_OVERLAP)
        texts = [str(c["text"]) for c in chunks]

        embeddings: list[list[float]] = []
        for j in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[j : j + EMBED_BATCH_SIZE]
            embeddings.extend(embed_batch(batch))

        for chunk_dict, vec in zip(chunks, embeddings):
            pending.append((chunk_dict, vec))
            if len(pending) >= QDRANT_UPSERT_BATCH:
                upsert_chunks(client, pending)
                total_points += len(pending)
                pending.clear()

        now = time.perf_counter()
        if now - last_progress_log >= PROGRESS_LOG_INTERVAL_SEC or idx == len(files):
            elapsed = int(now - t0)
            print(
                f"[ingest] Processing {idx}/{len(files)}: {path.name} "
                f"({len(chunks)} chunks, {elapsed}s elapsed)"
            )
            last_progress_log = now

    if pending:
        upsert_chunks(client, pending)
        total_points += len(pending)

    total_elapsed = int(time.perf_counter() - t0)
    print(
        f"[ingest] DONE. Collection '{QDRANT_COLLECTION}' contains {total_points} points. "
        f"Total time: {total_elapsed}s"
    )


if __name__ == "__main__":
    main()
