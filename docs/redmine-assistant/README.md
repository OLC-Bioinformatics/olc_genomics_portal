# Redmine Documentation Assistant: Operations Guide

This directory contains the operational documentation for the retrieval-only Redmine Documentation Assistant.

## System purpose

The assistant provides authenticated Redmine users with semantic search over the OLC Redmine Automator documentation. It is currently retrieval-only: it returns ranked documentation excerpts and links to the hosted documentation; it does not generate answers with a large language model.

## Current production architecture

```text
Authenticated Redmine user
        |
        v
Redmine Assistant project tab
        |
        v
Redmine plugin controller
        |
        |  POST http://rag:8001/search
        v
RAG API container
        |
        +--> BAAI/bge-small-en-v1.5 embedding model
        |
        +--> PostgreSQL 16 + pgvector
        |
        +--> read-only Markdown documentation mount
```

## Components

- **Redmine plugin:** `redmine/plugins/redmine_assistant`
- **RAG service:** `rag`
- **Vector database:** `rag-db`, using `pgvector/pgvector:pg16`
- **Documentation source:** mounted read-only at `/documentation`
- **Model cache:** Docker volume `rag-model-cache`, mounted at `/models`
- **Vector data:** Docker volume `rag-db`, mounted at `/var/lib/postgresql/data`
- **Hosted documentation:** `https://olc-bioinformatics.github.io/redmine-docs/`

## Validated baseline

The documentation-tuned dense retrieval baseline evaluates 142 questions:

- 136 answerable questions
- 6 no-answer questions
- Hit@1: 90.4%
- Hit@3: 98.5%
- Hit@5: 100.0%
- Expected content-term coverage: 94.7%
- Internal-document leakage: 0
- Authorized internal retrieval test: 100%

The baseline report is stored under `rag/evaluation-results/`.

## Common operator tasks

- [Configuration](configuration.md)
- [Deployment and startup](deployment.md)
- [Documentation indexing](indexing.md)
- [Testing and evaluation](testing.md)
- [Backup and restore](backup-and-restore.md)
- [Monitoring and routine operations](monitoring.md)
- [Security and access control](security.md)
- [Troubleshooting](troubleshooting.md)
- [Change and release procedure](release-procedure.md)

## Important safety rules

1. Never run Redmine tests against the production `redmine` database. Tests must use `redmine_test`.
2. Never expose the RAG API directly to users. Browser traffic must go through authenticated Redmine.
3. Never permit the public Redmine search route to enable internal-document retrieval.
4. Do not commit the `env` file, passwords, tokens, certificates containing private keys, database dumps, or model-cache contents.
5. Back up the RAG database before destructive schema or embedding-model changes.
6. Changing the embedding model, model revision, vector dimension, normalization, or chunking configuration requires a controlled full re-index.
