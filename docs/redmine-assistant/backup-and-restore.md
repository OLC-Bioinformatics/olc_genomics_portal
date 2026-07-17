# Backup and Restore

## Data requiring backup

### RAG PostgreSQL database

The `rag-db` Docker volume stores:

- schema migrations;
- indexed documents;
- chunks and metadata;
- embedding vectors;
- index metadata.

### Model cache

The `rag-model-cache` volume stores the pinned embedding model. The model can be restored from an approved artifact or downloaded again only when network access and policy permit.

### Source and configuration

Git should contain:

- RAG application code;
- migrations;
- Redmine plugin code;
- tests;
- evaluation questions and approved baseline reports;
- non-secret Compose configuration.

The documentation repository must be backed up through its normal source-control process.

## Logical PostgreSQL backup

Create a timestamped dump outside the database container:

```bash
mkdir -p backups/rag-db

backup_file="backups/rag-db/redmine_assistant_$(date -u +%Y%m%dT%H%M%SZ).dump"

docker compose exec -T rag-db \
  pg_dump \
  -U redmine_assistant \
  -d redmine_assistant \
  -Fc \
  > "$backup_file"

ls -lh "$backup_file"
```

Protect backups according to organizational requirements. Do not commit them to Git.

## Verify a backup

```bash
pg_restore --list "$backup_file" | head
```

If `pg_restore` is not installed on the host, use a temporary PostgreSQL 16 container with the backup mounted read-only.

## Restore approach

A restore should be tested first in a non-production environment.

High-level sequence:

1. Stop writes/indexing.
2. Create a fresh backup of the current database.
3. Recreate or clean the target database.
4. Restore the dump with PostgreSQL 16-compatible tools.
5. Start the RAG service.
6. Verify migrations, document count, chunk count, and vector dimensions.
7. Run semantic smoke tests and the evaluation suite.

Example restore into an empty target database:

```bash
cat "$backup_file" | docker compose exec -T rag-db \
  pg_restore \
  -U redmine_assistant \
  -d redmine_assistant \
  --clean \
  --if-exists \
  --no-owner
```

Use `--clean` only when the target and restoration plan have been reviewed. It is destructive.

## Model-cache recovery

Verify cached model contents:

```bash
docker compose exec rag du -sh /models
```

Verify offline loading:

```bash
docker compose exec -T -w /app rag python - <<'PY'
from embeddings import embedding_service

vector = embedding_service.embed_query(
    "Which automator detects plasmids?"
)

print(len(vector))
PY
```

Expected:

```text
384
```

If the model cache is lost while offline mode is enabled, the RAG service cannot load the model. Restore the volume or temporarily perform an approved model download, verify the pinned revision, and re-enable offline mode.
