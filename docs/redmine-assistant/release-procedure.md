# Change and Release Procedure

## 1. Classify the change

### Documentation-only

- Update the documentation repository.
- Run incremental indexing.
- Run focused and full retrieval evaluation if retrieval behavior may change.

### RAG code or dependency

- Build `rag`.
- Run Python tests.
- Apply migrations if present.
- Re-index if parsing, chunking, embeddings, or schema changed.
- Run retrieval evaluation.

### Redmine plugin

- Build `redmine`.
- Run `./scripts/test-redmine-assistant.sh`.
- Recreate Redmine.
- Verify plugin registration, role permission, UI, styles, and links.

### Embedding model or chunking configuration

- Back up `rag-db`.
- Record the new model name, revision, dimension, and license review.
- Build in non-production.
- Perform a full re-index.
- Save a new named evaluation baseline.
- Do not overwrite the previous baseline report.

## 2. Pre-release checks

```bash
./scripts/test-redmine-assistant.sh
```

```bash
docker compose exec \
  -w /app \
  -e PYTHONPYCACHEPREFIX=/tmp/redmine-assistant-pycache \
  rag \
  python -m pytest -q
```

Run the full evaluation and archive the JSON report.

Review:

```bash
git status --short
git diff --stat
git diff
```

Check for secrets:

```bash
git diff | grep -Ei \
  'password|secret|token|client_secret|private_key'
```

Manually review every match.

## 3. Build and deploy

```bash
docker compose build rag redmine
```

```bash
docker compose up -d --force-recreate rag redmine
```

## 4. Post-deployment validation

- Containers healthy.
- RAG readiness is 200.
- Redmine Assistant tab appears for an authorized role.
- Search returns expected results.
- Hosted documentation links work.
- Internal paths do not appear.
- Logs contain no new exceptions.

## 5. Commit and tag

Example commit:

```bash
git add <reviewed-files>
git diff --cached --stat
git diff --cached
git commit -m "docs: add Redmine assistant operations guide"
```

Optional milestone tag:

```bash
git tag redmine-assistant-retrieval-v0.1.0
```

## 6. Rollback criteria

Rollback when any of the following occurs:

- internal content is exposed;
- authentication or authorization is bypassed;
- readiness remains unhealthy;
- retrieval evaluation materially regresses without approval;
- Redmine cannot reach RAG;
- schema migration fails or corrupts data;
- user-facing errors expose confidential implementation details.
