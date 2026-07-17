# Troubleshooting

## RAG readiness returns 503

Check service state and logs:

```bash
docker compose ps rag rag-db
```

```bash
docker compose logs --tail 200 rag rag-db
```

Verify PostgreSQL:

```bash
docker compose exec rag-db \
  pg_isready \
  -U redmine_assistant \
  -d redmine_assistant
```

## Model fails to load in offline mode

Symptoms may mention missing Hugging Face files or inability to contact the Hub.

Check cache:

```bash
docker compose exec rag find /models -maxdepth 4 -type d | head
```

Expected pinned snapshot:

```text
5c38ec7c405ec4b44b94cc5a9bb96e735b38267a
```

Verify Compose has:

```text
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
MODEL_CACHE_DIR=/models
```

If the cache is missing, restore it or perform an approved one-time download of the pinned revision before re-enabling offline mode.

## Redmine shows “assistant temporarily unavailable”

Check Redmine can resolve and reach the RAG service:

```bash
docker compose exec redmine \
  ruby -rnet/http -e '
uri = URI("http://rag:8001/health/ready")
response = Net::HTTP.get_response(uri)
puts response.code
puts response.body
'
```

Check:

```text
REDMINE_ASSISTANT_RAG_URL=http://rag:8001
```

Do not use `127.0.0.1` inside Redmine.

## Assistant tab is missing

1. Confirm plugin registration.
2. Confirm routes exist.
3. Grant `view_redmine_assistant` to the user’s project role.
4. Confirm the user is a member of the project or otherwise authorized.
5. Restart Redmine after `init.rb` changes.

## Stylesheet not applied

Confirm the page head links a fingerprinted file under:

```text
/assets/plugin_assets/redmine_assistant/
```

Confirm compiled CSS exists:

```bash
docker compose exec redmine \
  find public/assets/plugin_assets/redmine_assistant \
  -type f -name '*.css' -print
```

A browser response of `304 Not Modified` is normal cache behavior. Use a hard refresh after deployment.

## Hosted documentation links are missing

Confirm the variable inside Redmine:

```bash
docker compose exec redmine \
  bash -lc 'printf "%s\n" "${REDMINE_ASSISTANT_DOCS_BASE_URL:-missing}"'
```

Expected:

```text
https://olc-bioinformatics.github.io/redmine-docs/
```

If the value is in the service `env_file`, ensure an empty Compose `environment` interpolation is not overriding it.

## Indexer skips a changed page

- Confirm the correct documentation repository is mounted.
- Confirm the source file exists inside `/documentation`.
- Check its checksum and timestamps.
- Run `python -m cli discover --show-paths`.
- Inspect indexer logs for parsing failures.

## Vector dimension mismatch

The schema and model must both use 384 dimensions. A model/configuration change requires a controlled full rebuild. Do not mix vectors with different dimensions or model revisions.

## Redmine plugin tests fail with `mocha/minitest` missing

The production image excludes test dependencies. Use:

```bash
./scripts/test-redmine-assistant.sh
```

The script installs test dependencies inside a disposable container.

## Test migration reports duplicate Entra ID columns

Do not run unrelated plugin migrations on every Assistant test execution. The routine test script should verify `redmine_test` and run only the test files. The Assistant plugin currently has no database migrations.

## Host curl reports an unknown certificate issuer

The browser may still work because it trusts the organizational CA. For command-line testing, use the approved CA bundle. Avoid `curl -k` in automated or production checks because it disables certificate verification.
