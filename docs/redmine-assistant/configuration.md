# Configuration

## Required Compose services

The assistant depends on these services:

- `redmine`
- `rag`
- `rag-db`
- `mariadb`

The Redmine service reaches the RAG API over the Compose network at `http://rag:8001`. Do not use `127.0.0.1` from within the Redmine container.

## RAG service variables

Configure these under the `rag` service or its environment file:

```text
RAG_DB_HOST=rag-db
RAG_DB_PORT=5432
RAG_DB_NAME=redmine_assistant
RAG_DB_USER=redmine_assistant
RAG_DB_PASSWORD=<secret>
DOCUMENTATION_ROOT=/documentation
RAG_TOP_K=5
RAG_MAX_TOP_K=10
RAG_WORKERS=1
RAG_THREADS=4
RAG_TIMEOUT=60
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
EMBEDDING_MODEL_REVISION=5c38ec7c405ec4b44b94cc5a9bb96e735b38267a
EMBEDDING_DIMENSION=384
EMBEDDING_DEVICE=cpu
EMBEDDING_BATCH_SIZE=32
EMBEDDING_NORMALIZE=true
MODEL_CACHE_DIR=/models
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

### Notes

- Keep `RAG_WORKERS=1` initially. Each worker process can load an independent model copy.
- The configured model produces 384-dimensional vectors and is pinned to a specific revision.
- Offline mode depends on the pinned model already being present in `rag-model-cache`.
- `RAG_MAX_TOP_K` is enforced by the API.

## Redmine plugin variables

Configure these for the `redmine` service:

```text
REDMINE_ASSISTANT_RAG_URL=http://rag:8001
REDMINE_ASSISTANT_TIMEOUT_SECONDS=15
REDMINE_ASSISTANT_DEFAULT_LIMIT=5
REDMINE_ASSISTANT_DOCS_BASE_URL=https://olc-bioinformatics.github.io/redmine-docs/
```

If `REDMINE_ASSISTANT_DOCS_BASE_URL` is defined in the service `env_file`, do not add an empty Compose interpolation that overrides it.

For example, remove this if the host shell does not define the variable:

```yaml
- REDMINE_ASSISTANT_DOCS_BASE_URL=${REDMINE_ASSISTANT_DOCS_BASE_URL:-}
```

## Persistent storage

```yaml
volumes:
  rag-db:
  rag-model-cache:
```

- `rag-db` contains PostgreSQL/pgvector data.
- `rag-model-cache` contains the embedding model files.
- Documentation is bind-mounted read-only from the documentation repository.

## Secrets

The following must remain outside Git:

- `RAG_DB_PASSWORD`
- MariaDB passwords
- Entra ID client secret
- SMTP password
- authentication tokens
- database dumps containing sensitive data

Use the existing deployment secret-management approach or a non-versioned environment file with restrictive permissions.
