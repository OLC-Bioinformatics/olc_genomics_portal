# Deployment and Startup

## Build modified services

After RAG application or dependency changes:

```bash
docker compose build rag
```

After Redmine plugin, Redmine Dockerfile, helper, view, locale, or stylesheet changes:

```bash
docker compose build redmine
```

## Recreate services

```bash
docker compose up -d --force-recreate rag redmine
```

Redmine depends on a healthy RAG service. The RAG service depends on a healthy `rag-db` service.

## Verify service status

```bash
docker compose ps redmine rag rag-db
```

Expected:

- `redmine`: running
- `rag`: healthy
- `rag-db`: healthy

## Verify RAG readiness

```bash
curl -sS http://127.0.0.1:8001/health/ready
echo
```

A healthy response includes:

```json
{
  "status": "ready",
  "database": "connected",
  "schema_migrations": 2,
  "documents": 51,
  "chunks": 405
}
```

Document and chunk counts can change when documentation changes.

## Verify Redmine-to-RAG connectivity

```bash
docker compose exec redmine \
  ruby -rnet/http -rjson -e '
uri = URI("http://rag:8001/health/ready")
response = Net::HTTP.get_response(uri)
puts "HTTP #{response.code}"
puts JSON.pretty_generate(JSON.parse(response.body))
'
```

## Verify plugin registration

```bash
docker compose exec redmine \
  bundle exec rails runner \
  'puts Redmine::Plugin.find(:redmine_assistant).name'
```

Expected:

```text
Redmine Documentation Assistant
```

## Verify routes

```bash
docker compose exec redmine \
  bundle exec rails routes \
  | grep redmine_assistant
```

Expected routes:

```text
GET  /projects/:project_id/redmine_assistant
POST /projects/:project_id/redmine_assistant/search
```

## Verify plugin stylesheet

```bash
docker compose exec redmine \
  bash -lc '
    find public/assets/plugin_assets/redmine_assistant \
      -type f \
      -name "redmine_assistant-*.css" \
      -print
  '
```

The page should load a fingerprinted stylesheet under `/assets/plugin_assets/redmine_assistant/`.

## Rollback

1. Check out the previous known-good Git commit.
2. Rebuild the affected service image.
3. Recreate the service.
4. Verify health and run smoke tests.
5. If a database migration must be reversed, restore from a verified backup or follow the migration-specific rollback plan. Do not improvise destructive SQL in production.
