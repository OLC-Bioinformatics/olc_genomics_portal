# Redmine Documentation Assistant Operations Guide

This directory is the operator manual for the retrieval-only Redmine Documentation Assistant. The assistant performs permission-aware semantic search over the OLC Redmine Automator Markdown documentation. It does not yet generate answers with a large language model or create Redmine issues.

## Architecture

```text
Authenticated Redmine user
        |
        v
Redmine Assistant project tab
        |
        v
Redmine plugin controller and server-side RagClient
        |  Bearer service token + trusted access header
        v
POST http://rag:8001/api/v1/retrieve
        |
        +--> BAAI/bge-small-en-v1.5 query embedding
        +--> PostgreSQL 16 + pgvector similarity search
        +--> standard/internal access filtering
        +--> minimum-similarity filtering
        +--> retrieval request and feedback persistence
```

The browser never receives the trusted RAG service token and never calls the RAG API directly. Redmine derives `standard` or `internal` access from project permissions. The RAG service enforces the access context again.

## Durable and rebuildable state

- `rag-db` is durable PostgreSQL state. It stores migrations, indexed documents, chunks, vectors, retrieval requests, and helpfulness feedback.
- `rag-model-cache` stores the pinned embedding model and can be restored from an approved artifact.
- Markdown source files are mounted read-only at `/documentation` and remain authoritative.
- The vector index is rebuildable from Markdown. Retrieval requests and feedback are not rebuildable and must not be deleted by reindexing.
- `docker compose down -v` deletes named volumes and therefore deletes RAG database and model-cache state.

## Current retrieval gate

`RAG_MINIMUM_SIMILARITY` defaults to `0.60` in this deployment. Results below the threshold are omitted before `LIMIT` is applied. The displayed score is cosine similarity, not a calibrated probability or confidence score.

## Quick health check

```bash
docker compose ps redmine rag rag-db mariadb
curl -sS http://127.0.0.1:8001/health/ready | python3 -m json.tool
docker compose exec rag python -m cli status
```

## Quick functional checks

```bash
# Expected to return useful sources.
curl -sS -H 'Content-Type: application/json' \
  -d '{"query":"Which automator detects plasmids?","limit":5}' \
  http://127.0.0.1:8001/api/v1/retrieve | python3 -m json.tool

# Expected to return result_count 0 and sources [].
curl -sS -H 'Content-Type: application/json' \
  -d '{"query":"bacon","limit":5}' \
  http://127.0.0.1:8001/api/v1/retrieve | python3 -m json.tool
```

## Documentation map

- [Configuration](configuration.md)
- [Project restriction](project-restriction.md)
- [Deployment and startup](deployment.md)
- [Operations runbook](operations-runbook.md)
- [Documentation indexing](indexing.md)
- [Testing](testing.md)
- [Retrieval evaluation](evaluation.md)
- [Helpfulness feedback](feedback.md)
- [Monitoring](monitoring.md)
- [Backup and restore](backup-and-restore.md)
- [Security](security.md)
- [Troubleshooting](troubleshooting.md)
- [Release procedure](release-procedure.md)

## Safety rules

1. Never run Redmine tests until `RAILS_ENV=test` resolves to `redmine_test`.
2. Never expose the trusted service token to JavaScript, HTML, logs, or Git.
3. Never permit browser parameters to select `internal` access.
4. Back up `rag-db` before destructive database, schema, embedding-model, or volume work.
5. Treat user queries and feedback comments as potentially sensitive operational data.
6. Changing the embedding model, revision, dimension, normalization, or material chunking behavior requires a controlled full reindex and evaluation.
