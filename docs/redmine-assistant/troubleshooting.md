# Troubleshooting

## RAG readiness returns 503

```bash
docker compose ps rag rag-db
docker compose logs --tail 200 rag rag-db
docker compose exec rag-db pg_isready -U redmine_assistant -d redmine_assistant
docker compose exec rag python -m cli status
```

Check database credentials, pending migration errors, and database volume health.

## Redmine reports the assistant is unavailable

```bash
docker compose exec redmine ruby -rnet/http -e '
u=URI("http://rag:8001/health/ready"); r=Net::HTTP.get_response(u); puts r.code; puts r.body'

docker compose exec redmine sh -lc 'printf "%s\n" "$REDMINE_ASSISTANT_RAG_URL"'
```

Use `http://rag:8001`, not `127.0.0.1`, inside Redmine.

## RAG configuration change is ignored

```bash
docker compose up -d --force-recreate rag
docker compose exec rag python -c \
  'from config import settings; print(settings.minimum_similarity)'
```

A restart does not apply changed Compose environment values; recreate the container.

## Irrelevant results are returned

1. Confirm `RAG_MINIMUM_SIMILARITY` is loaded.
2. Confirm `rag/retrieval.py` filters `score >= settings.minimum_similarity` before `LIMIT`.
3. Test the API directly.
4. Add the query as an abstention evaluation case.
5. Change the threshold only after positive/negative evaluation.

```bash
curl -sS -H 'Content-Type: application/json' \
  -d '{"query":"bacon","limit":5}' \
  http://127.0.0.1:8001/api/v1/retrieve | python3 -m json.tool
```

## Valid queries return no results

Inspect the loaded threshold, run the query with the CLI, review zero-result telemetry, and evaluate a lower threshold. Do not remove the gate based on one query.

## Feedback is not recorded

```bash
docker compose logs --tail 200 redmine rag
docker compose exec rag-db psql -U redmine_assistant -d redmine_assistant -c \
  '\dt retrieval*'
```

Verify migration `003_add_retrieval_feedback.sql`, the trusted token in both containers, the plugin feedback route, and that the original retrieval request exists. A mismatched access context is intentionally treated as not found.

## Redmine plugin changes are not visible

The plugin is copied into the image:

```bash
docker compose up -d --build redmine
```

Verify host/container checksums for the affected file.

## Redmine tests cannot load `mocha/minitest`

The image excluded test gems. The current simple Dockerfile approach uses:

```dockerfile
RUN bundle config unset without && \
    bundle install
```

Rebuild Redmine, verify `bundle show mocha`, and confirm tests use `redmine_test`.

## Model fails in offline mode

```bash
docker compose exec rag du -sh /models
docker compose exec rag find /models -maxdepth 4 -type d | head
```

Restore the approved pinned model cache. Do not disable TLS verification or silently download an unpinned model in production.

## Indexer skips a changed document

```bash
docker compose exec -w /app rag python -m cli discover --show-paths
docker compose exec -w /app rag python -m cli dry-run-index \
  --source analysis/example.md --show-content
docker compose exec -w /app rag python -m cli index
```

Confirm the correct repository mount, path, parse success, checksum, and index logs.
