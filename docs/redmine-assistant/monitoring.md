# Monitoring and Routine Operations

## Daily or routine checks

```bash
docker compose ps redmine rag rag-db mariadb
```

```bash
curl -sS http://127.0.0.1:8001/health/ready
echo
```

Check recent errors:

```bash
docker compose logs \
  --since 24h \
  redmine rag rag-db \
  | grep -Ei 'error|exception|traceback|fatal'
```

Review matches; expected test or warning messages may not indicate an outage.

## Readiness meaning

The RAG readiness endpoint confirms:

- the Flask/Gunicorn application is running;
- PostgreSQL is reachable;
- schema metadata is readable;
- document and chunk counts can be reported.

It does not guarantee that a user query returns the correct semantic result. Use smoke tests or evaluation for functional validation.

## Logs

Follow live service logs:

```bash
docker compose logs -f --tail 100 redmine rag
```

Normal search flow should show:

- an authenticated POST to the Redmine assistant search route;
- a server-side POST to RAG `/search`;
- an HTTP 200 response;
- no internal documentation paths.

## Capacity baseline

The validated VM had:

- x86_64 architecture;
- 4 CPUs;
- 15 GiB RAM;
- no swap;
- ample disk space;
- a model cache of approximately 129 MiB.

Keep one RAG worker initially to avoid loading several model copies. Threads can provide concurrency within that process.

## First-request behavior

The first embedding request after a RAG process restart loads the model and will be slower. Later requests in the same worker reuse it.

## Disk checks

```bash
df -h /var/lib/docker
```

```bash
docker system df
```

Do not use indiscriminate Docker volume pruning. The named volumes contain persistent database and model data.

## Database checks

```bash
docker compose exec rag python -m cli status
```

```bash
docker compose exec rag-db \
  psql \
  -U redmine_assistant \
  -d redmine_assistant \
  -c "SELECT COUNT(*) FROM documents;
      SELECT COUNT(*) FROM document_chunks;"
```

## Suggested alert conditions

- `rag` or `rag-db` is unhealthy for more than several health intervals;
- readiness returns 503;
- document or chunk count unexpectedly becomes zero;
- indexing reports failures;
- Redmine repeatedly reports RAG timeout/unavailable errors;
- disk usage approaches the organization’s threshold;
- evaluation reports internal leakage or material retrieval regression.
