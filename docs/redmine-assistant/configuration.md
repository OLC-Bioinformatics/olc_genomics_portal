# Configuration

## Inspect effective Compose configuration

```bash
docker compose config > /tmp/foodport-compose-effective.yml
grep -A 45 '^  rag:' /tmp/foodport-compose-effective.yml
```

Do not publish the effective file; Compose may interpolate secrets.

## RAG environment variables

The following are configured under `services.rag.environment` or `env_file`.

- `RAG_DB_HOST` (`rag-db`): PostgreSQL service name.
- `RAG_DB_PORT` (`5432`): PostgreSQL port.
- `RAG_DB_NAME` (`redmine_assistant`): database name.
- `RAG_DB_USER` (`redmine_assistant`): database role.
- `RAG_DB_PASSWORD` (required): database password; keep outside Git.
- `DOCUMENTATION_ROOT` (`/documentation`): read-only Markdown root.
- `MODEL_CACHE_DIR` (`/models`): embedding-model cache.
- `RAG_TOP_K` (`5`): default number of results.
- `RAG_MAX_TOP_K` (`10`): maximum accepted result count.
- `RAG_MAX_QUERY_CHARS` (`2000`): maximum query length.
- `RAG_MAX_EXCERPT_CHARS` (`1500`): maximum v1 API excerpt length.
- `RAG_MINIMUM_SIMILARITY` (`0.60`): minimum cosine similarity accepted by retrieval. Valid configured range is `-1.0` through `1.0`.
- `RAG_TRUSTED_SERVICE_TOKEN` (required for trusted calls): shared Redmine-to-RAG Bearer token.
- `RAG_TRUSTED_ACCESS_HEADER` (`X-Redmine-Assistant-Access`): trusted access-context header name.
- `RAG_WORKERS` (`1`): Gunicorn workers. Keep at one initially because each worker may load a model copy.
- `RAG_THREADS` (`4`): Gunicorn threads.
- `RAG_TIMEOUT` (`60`): Gunicorn timeout seconds.
- `EMBEDDING_MODEL` (`BAAI/bge-small-en-v1.5`): model name.
- `EMBEDDING_MODEL_REVISION`: pinned model revision.
- `EMBEDDING_DIMENSION` (`384`): vector dimension; must match the database column.
- `EMBEDDING_DEVICE` (`cpu`): inference device.
- `EMBEDDING_BATCH_SIZE` (`32`): indexing batch size.
- `EMBEDDING_NORMALIZE` (`true`): normalization behavior.
- `HF_HUB_OFFLINE` and `TRANSFORMERS_OFFLINE` (`1`): prohibit runtime downloads.

## Redmine plugin environment variables

- `REDMINE_ASSISTANT_RAG_URL` (`http://rag:8001`): internal service URL.
- `REDMINE_ASSISTANT_TIMEOUT_SECONDS` (`15`): open/read/write timeout.
- `REDMINE_ASSISTANT_DEFAULT_LIMIT` (`5`): default result count.
- `REDMINE_ASSISTANT_DOCS_BASE_URL`: hosted documentation URL, normally supplied by `env`.
- `RAG_TRUSTED_SERVICE_TOKEN`: must match the RAG container value.
- `RAG_TRUSTED_ACCESS_HEADER`: must match the RAG container value when customized.

## Verify live values without revealing secrets

```bash
docker compose exec rag python - <<'PY'
from config import settings
print(f'top_k={settings.top_k}')
print(f'max_top_k={settings.max_top_k}')
print(f'minimum_similarity={settings.minimum_similarity}')
print(f'max_query_chars={settings.max_query_chars}')
print(f'max_excerpt_chars={settings.max_excerpt_chars}')
print(f'embedding_model={settings.embedding_model}')
print(f'embedding_revision={settings.embedding_model_revision}')
print(f'embedding_dimension={settings.embedding_dimension}')
print(f'trusted_token_configured={bool(settings.trusted_service_token)}')
PY

docker compose exec redmine sh -lc '
  test -n "$RAG_TRUSTED_SERVICE_TOKEN" && echo token=configured || echo token=missing
  printf "rag_url=%s\n" "$REDMINE_ASSISTANT_RAG_URL"
'
```

## Apply configuration changes

Changing Compose environment values requires container recreation, not only restart:

```bash
docker compose up -d --force-recreate rag redmine
```

Changing Python files under `./rag` only requires a RAG restart because `/app` is bind-mounted:

```bash
docker compose restart rag
```

Changing the Redmine plugin requires an image rebuild because the plugin is copied by the Redmine Dockerfile:

```bash
docker compose up -d --build redmine
```

## Threshold calibration

Do not select `RAG_MINIMUM_SIMILARITY` from one nonsense query. Evaluate valid paraphrases, unsupported bioinformatics questions, and unrelated questions. Compare candidate thresholds using [evaluation.md](evaluation.md). A threshold change does not require reindexing, but it does require RAG container recreation and a regression evaluation.
