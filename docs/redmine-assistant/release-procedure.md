# Change and Release Procedure

## 1. Review the change

```bash
git status --short
git diff --check
git diff --stat
git diff
```

Remove temporary bundles, patches, dumps, and CSV exports. Review every apparent secret match manually:

```bash
git diff | grep -Ei 'password|secret|token|client_secret|private_key' || true
```

## 2. Run tests

```bash
docker compose exec rag pytest -q

docker compose exec redmine bundle exec rails runner -e test \
  'db=ActiveRecord::Base.connection_db_config.database; puts db; abort unless db == "redmine_test"'

docker compose exec redmine bundle exec rake redmine:plugins:test \
  NAME=redmine_assistant RAILS_ENV=test
```

## 3. Run evaluation

```bash
docker compose exec -w /app rag python -m cli evaluate \
  --top-k 5 \
  --output-json /tmp/evaluation-release.json

docker compose cp rag:/tmp/evaluation-release.json \
  rag/evaluation-results/evaluation-release.json
```

Review Hit@1/3/5, strict source requirements, term coverage, abstentions, and leakage before accepting changes.

## 4. Back up when durable state or schema changes

Follow [backup-and-restore.md](backup-and-restore.md). Confirm the dump and checksum exist outside the container.

## 5. Build and deploy

```bash
docker compose build rag redmine
docker compose up -d --force-recreate rag redmine
docker compose logs --tail 100 rag redmine
```

## 6. Validate

```bash
docker compose ps redmine rag rag-db
curl -sS http://127.0.0.1:8001/health/ready | python3 -m json.tool
```

Perform positive and abstention searches in the API and browser. Submit feedback and verify it in PostgreSQL. Confirm standard users cannot retrieve internal documents.

## 7. Commit and tag

```bash
git add <reviewed-files>
git diff --cached --check
git diff --cached --stat
git diff --cached
git commit -m "docs(redmine-assistant): update operations guide"
```

Tag only approved milestones using the repository's versioning convention.

## Rollback criteria

Rollback for internal-content exposure, authentication/authorization bypass, migration failure, persistent unhealthy services, material retrieval regression, feedback data loss, or user-facing disclosure of confidential implementation details.
