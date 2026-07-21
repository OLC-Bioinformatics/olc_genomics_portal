# Documentation Indexing

## Discover source files

```bash
docker compose exec -w /app rag python -m cli discover --show-paths
```

The source mount is read-only at `/documentation`. Standard and internal documents must carry the intended access metadata; internal source paths are also defensively recognized by the integration.

## Inspect parsing and chunking without database writes

```bash
docker compose exec -w /app rag python -m cli dry-run-index
```

Inspect one document and its generated chunks:

```bash
docker compose exec -w /app rag python -m cli dry-run-index \
  --source analysis/confindr.md \
  --show-content
```

Optional chunk-size experiments:

```bash
docker compose exec -w /app rag python -m cli dry-run-index \
  --target-chars 1200 \
  --max-chars 1800
```

## Back up before risky work

```bash
mkdir -p backups/rag-db
backup_file="backups/rag-db/redmine_assistant_$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T rag-db pg_dump \
  -U redmine_assistant -d redmine_assistant -Fc > "$backup_file"
pg_restore --list "$backup_file" | head
```

## Run incremental indexing

```bash
docker compose exec -w /app rag python -m cli index
```

Run it again to verify idempotence. With unchanged documentation, added, updated, removed, and embedded counts should be zero while unchanged equals discovered.

## Verify index state

```bash
docker compose exec rag python -m cli status

docker compose exec rag-db psql \
  -U redmine_assistant -d redmine_assistant -tAc \
  'SELECT COUNT(*), COUNT(embedding) FROM document_chunks;'

docker compose exec rag-db psql \
  -U redmine_assistant -d redmine_assistant -tAc \
  'SELECT vector_dims(embedding), COUNT(*) FROM document_chunks GROUP BY vector_dims(embedding);'
```

All chunks should have embeddings and the vector dimension should be `384` for the current model.

## Search from the CLI

```bash
docker compose exec -w /app rag python -m cli search \
  'How do I check FASTQ files for contamination?' \
  --limit 5
```

Maintainer-only diagnostic search:

```bash
docker compose exec -w /app rag python -m cli search \
  'How does the internal merge workflow work?' \
  --limit 5 \
  --include-internal
```

Do not expose `--include-internal` as a browser option.

## When reindexing is required

Run incremental indexing after Markdown additions, edits, moves, or removals. Perform a controlled full rebuild after changing the embedding model, revision, vector dimension, normalization, embedding-content construction, or material parsing/chunking behavior. Preserve `retrieval_requests` and `retrieval_feedback`; they are operational history, not rebuildable index tables.
