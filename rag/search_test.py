"""Quick retrieval smoke-test after ingestion (TEI embed + Qdrant search)."""

from __future__ import annotations

import os
import sys

import requests
from qdrant_client import QdrantClient

QDRANT_URL = os.getenv("QDRANT_URL", "http://192.168.30.127:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "build123d_docs")
TEI_URL = os.getenv("TEI_URL", "http://192.168.30.121:8080")
VECTOR_SIZE = 1024


def _normalize_embed_response(data: object) -> list[list[float]]:
    if isinstance(data, list):
        if data and isinstance(data[0], (int, float)):
            return [list(map(float, data))]
        return [[float(x) for x in row] for row in data if isinstance(row, list)]
    if isinstance(data, dict) and "embeddings" in data:
        return _normalize_embed_response(data["embeddings"])
    print(f"[search_test] Unexpected TEI response: {type(data)}", file=sys.stderr)
    sys.exit(1)


def embed_query(text: str) -> list[float]:
    url = f"{TEI_URL.rstrip('/')}/embed"
    r = requests.post(url, json={"inputs": [text]}, timeout=120)
    if r.status_code != 200:
        print(f"[search_test] TEI error HTTP {r.status_code}: {r.text}", file=sys.stderr)
        sys.exit(1)
    vectors = _normalize_embed_response(r.json())
    if not vectors or len(vectors[0]) != VECTOR_SIZE:
        print("[search_test] Invalid embedding dimensions.", file=sys.stderr)
        sys.exit(1)
    return vectors[0]


def main() -> None:
    queries = [
        "comment faire un fillet sur les arêtes d'une boîte",
        "anneau demi-rond avec révolution",
        "sélectionner la face supérieure d'une pièce",
        "créer un compound de plusieurs solides",
        "extruder une esquisse",
    ]

    client = QdrantClient(url=QDRANT_URL.rstrip("/"))

    for query in queries:
        vector = embed_query(query)
        resp = client.query_points(
            collection_name=QDRANT_COLLECTION,
            query=vector,
            limit=5,
        )
        hits = resp.points
        print(f"\n=== {query} ===")
        for rank, hit in enumerate(hits, start=1):
            payload = dict(hit.payload or {})
            source = payload.get("source_file", "?")
            text = str(payload.get("text", ""))[:200].replace("\n", " ")
            print(f"  {rank}. score={hit.score:.4f} source={source}")
            print(f"     preview: {text}")


if __name__ == "__main__":
    main()
