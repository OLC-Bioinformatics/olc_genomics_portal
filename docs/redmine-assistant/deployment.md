# Deployment and Startup

## Pre-deployment checks

```bash
git status --short
docker compose config --quiet
docker compose exec rag pytest -q
```

Before Redmine tests, verify the test database:

```bash
docker compose exec redmine bundle exec rails runner -e test \
  'puts ActiveRecord::Base.connection_db_config.database'
```

Expected: `redmine_test`.

## Build and deploy

```bash
docker compose build rag redmine
docker compose up -d --force-recreate rag redmine
```

The RAG startup script waits for PostgreSQL, runs pending SQL migrations, and starts Gunicorn. Redmine waits for RAG readiness.

## Verify containers and migrations

```bash
docker compose ps redmine rag rag-db mariadb
docker compose logs --tail 100 rag redmine
curl -sS http://127.0.0.1:8001/health/ready | python3 -m json.tool

docker compose exec rag-db psql \
  -U redmine_assistant -d redmine_assistant \
  -c 'SELECT version, applied_at FROM schema_migrations ORDER BY applied_at;'
```

## Verify plugin registration and routes

```bash
docker compose exec redmine bundle exec rails runner \
  'puts Redmine::Plugin.find(:redmine_assistant).name'

docker compose exec redmine bundle exec rails routes | grep redmine_assistant
```

Expected routes include index, search, and feedback.

## Smoke tests

```bash
curl -sS -H 'Content-Type: application/json' \
  -d '{"query":"Which automator detects plasmids?","limit":3}' \
  http://127.0.0.1:8001/api/v1/retrieve | python3 -m json.tool

curl -sS -H 'Content-Type: application/json' \
  -d '{"query":"bacon","limit":5}' \
  http://127.0.0.1:8001/api/v1/retrieve | python3 -m json.tool
```

In Redmine, confirm the Assistant tab, a useful positive result, a no-result query, source links, and helpful/unhelpful submission.

## Rollback

1. Back up `rag-db` if schema or durable data changed.
2. Check out the previous known-good Git commit.
3. Rebuild affected images.
4. Recreate services.
5. Verify readiness, migrations, retrieval, permissions, and feedback.
6. Restore the database only when the migration-specific rollback plan requires it.

Never improvise destructive SQL against production.
