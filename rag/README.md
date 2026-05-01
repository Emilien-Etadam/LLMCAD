# RAG Build123d — ingestion locale

Pipeline minimal pour indexer la documentation Build123d (RST + exemples Python) dans **Qdrant** via des embeddings **TEI** (ex. BGE-M3, 1024 dimensions).

## Prérequis

- **Qdrant** joignable (ex. `http://192.168.30.127:6333`, endpoint `GET /healthz` → 200).
- **TEI** (text-embeddings-inference) joignable (ex. `http://192.168.30.121:8080`, `GET /health` → 200).
- Dépot **build123d** cloné avec le dossier `docs/` (par défaut `~/build123d-source/docs`).

## Installation

Depuis la racine du projet LLMCAD :

```bash
pip install -r rag/requirements.txt
```

(Alternative : créer un venv dédié, activer, puis la même commande.)

## Ingestion

```bash
python rag/ingest.py
```

Le script vérifie les services **avant** toute écriture, supprime la collection cible si elle existe, puis la recrée et ingère tous les `*.rst` et `*.py` sous le chemin docs.

### Variables d'environnement

| Variable | Défaut |
|----------|--------|
| `QDRANT_URL` | `http://192.168.30.127:6333` |
| `QDRANT_COLLECTION` | `build123d_docs` |
| `TEI_URL` | `http://192.168.30.121:8080` |
| `BUILD123D_DOCS_PATH` | `~/build123d-source/docs` |
| `CHUNK_TOKENS` | `500` |
| `CHUNK_OVERLAP` | `50` |
| `EMBED_BATCH_SIZE` | `8` |

Exemple avec une collection de test :

```bash
QDRANT_COLLECTION=test_build123d python rag/ingest.py
```

## Test retrieval

```bash
python rag/search_test.py
```

Les mêmes variables `QDRANT_*` et `TEI_URL` s’appliquent.

## Limites connues

- **Chunking naïf** par tokens (tiktoken), sans structure RST/Python.
- Chaque run **détruit et recrée** la collection configurée (idempotent mais destructif).
