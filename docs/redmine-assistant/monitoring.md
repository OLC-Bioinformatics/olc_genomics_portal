# Monitoring and Routine Operations

## Routine service checks

```bash
docker compose ps redmine rag rag-db mariadb
curl -sS http://127.0.0.1:8001/health/ready | python3 -m json.tool
docker compose exec rag python -m cli status
```

## Logs

```bash
docker compose logs --since 24h redmine rag rag-db \
  | grep -Ei 'error|exception|traceback|fatal|unavailable|forbidden'

docker compose logs -f --tail 100 redmine rag
```

Use `request_id` to correlate Redmine and RAG events. Do not expose raw exception messages or tokens to users.

## Database state

```bash
docker compose exec rag-db psql -U redmine_assistant -d redmine_assistant -c "
SELECT (SELECT COUNT(*) FROM documents) AS documents,
       (SELECT COUNT(*) FROM document_chunks) AS chunks,
       (SELECT COUNT(*) FROM retrieval_requests) AS searches,
       (SELECT COUNT(*) FROM retrieval_feedback) AS feedback;"
```

## Retrieval behavior

Review these weekly during testing:

- unhelpful ratings and comments;
- zero-result queries;
- feedback participation rate;
- valid queries close to the relevance threshold;
- access-control and leakage evaluation;
- index document/chunk counts.

Commands are in [feedback.md](feedback.md) and [evaluation.md](evaluation.md).

## Capacity

Keep one RAG worker initially. Each worker may load its own embedding-model instance. The first query after process restart is slower because the model is loaded lazily. Monitor host memory, CPU, disk, and Docker usage:

```bash
docker stats --no-stream rag redmine rag-db
df -h /var/lib/docker
docker system df
```

Do not use indiscriminate volume pruning. `rag-db`, `redmine-db`, and `rag-model-cache` contain persistent state.

## Suggested alerts

Alert when readiness returns 503, `rag` or `rag-db` remains unhealthy, document/chunk counts become zero, migrations fail, indexing reports failures, Redmine repeatedly reports RAG timeouts, internal leakage tests fail, or disk usage crosses the organizational threshold.
