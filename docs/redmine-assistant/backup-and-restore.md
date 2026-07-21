# Backup and Restore

## Data classification

Back up `rag-db` because it contains schema migrations, index state, retrieval requests, and feedback. The vector index can be rebuilt, but request and feedback history cannot. Back up the Markdown repository through normal source control. Preserve the pinned model artifact or `rag-model-cache` according to deployment policy.

## Create a logical backup

```bash
mkdir -p backups/rag-db
backup_file="backups/rag-db/redmine_assistant_$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T rag-db pg_dump \
  -U redmine_assistant -d redmine_assistant -Fc > "$backup_file"
ls -lh "$backup_file"
```

## Verify the backup

```bash
docker run --rm -v "$PWD/backups/rag-db:/backup:ro" postgres:16 \
  pg_restore --list "/backup/$(basename "$backup_file")" | head
sha256sum "$backup_file" > "$backup_file.sha256"
sha256sum -c "$backup_file.sha256"
```

Protect dumps and checksums according to organizational policy; do not commit them.

## Restore procedure

Test restores in a non-production environment first.

1. Stop RAG writes/indexing.
2. Back up the current database.
3. Recreate or clean the approved target database.
4. Restore with PostgreSQL 16-compatible tools.
5. Start RAG and allow migrations to run.
6. Verify document, chunk, retrieval-request, feedback, and migration counts.
7. Run API smoke tests and the retrieval evaluation.

Example destructive restore into an approved empty target:

```bash
cat "$backup_file" | docker compose exec -T rag-db pg_restore \
  -U redmine_assistant \
  -d redmine_assistant \
  --clean --if-exists --no-owner
```

Use `--clean` only after review; it drops restored objects.

## Post-restore verification

```bash
docker compose up -d rag
curl -sS http://127.0.0.1:8001/health/ready | python3 -m json.tool

docker compose exec rag-db psql -U redmine_assistant -d redmine_assistant -c "
SELECT (SELECT COUNT(*) FROM documents) AS documents,
       (SELECT COUNT(*) FROM document_chunks) AS chunks,
       (SELECT COUNT(*) FROM retrieval_requests) AS searches,
       (SELECT COUNT(*) FROM retrieval_feedback) AS feedback;"
```

## Volume cautions

`docker compose down` preserves named volumes. `docker compose down -v`, `docker volume rm`, and aggressive pruning can destroy database and model-cache state. Docker volumes are persistence mechanisms, not backups.
