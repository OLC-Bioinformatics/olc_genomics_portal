# Operations Runbook

Run commands from the repository root.

## Start or recreate the assistant

```bash
docker compose up -d rag-db rag redmine
docker compose ps rag-db rag redmine
```

## Rebuild after code changes

```bash
docker compose up -d --build rag redmine
```

## Apply changed environment values

```bash
docker compose up -d --force-recreate rag redmine
```

## Health and logs

```bash
curl -sS http://127.0.0.1:8001/health/ready | python3 -m json.tool
docker compose logs -f --tail 100 rag redmine
```

## Discover, inspect, and index documentation

```bash
docker compose exec -w /app rag python -m cli discover --show-paths
docker compose exec -w /app rag python -m cli dry-run-index
docker compose exec -w /app rag python -m cli index
docker compose exec rag python -m cli status
```

## Search from the CLI

```bash
docker compose exec -w /app rag python -m cli search \
  'Which automator detects plasmids?' --limit 5
```

## Test

```bash
docker compose exec rag pytest -q
docker compose exec redmine bundle exec rake redmine:plugins:test \
  NAME=redmine_assistant RAILS_ENV=test
```

## Evaluate

```bash
docker compose exec -w /app rag python -m cli evaluate \
  --top-k 5 --output-json /tmp/evaluation-current.json
docker compose cp rag:/tmp/evaluation-current.json \
  rag/evaluation-results/evaluation-current.json
```

## Review feedback

```bash
docker compose exec rag-db psql -U redmine_assistant -d redmine_assistant -c "
SELECT rr.created_at, rr.query, rr.result_count,
       rf.rating, rf.reason, rf.comment
FROM retrieval_requests rr
LEFT JOIN retrieval_feedback rf ON rf.request_id=rr.request_id
ORDER BY rr.created_at DESC LIMIT 50;"
```

## Back up

```bash
mkdir -p backups/rag-db
backup_file="backups/rag-db/redmine_assistant_$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T rag-db pg_dump \
  -U redmine_assistant -d redmine_assistant -Fc > "$backup_file"
```

## Stop safely

```bash
docker compose stop redmine rag
```

Do not use `docker compose down -v` unless intentional volume destruction has been approved and verified backups exist.
