# Testing

## RAG test suite

```bash
docker compose exec rag pytest -q
```

Current verified baseline after feedback and similarity filtering: `125 passed`. Treat this count as descriptive; update it as tests are added.

Targeted suites:

```bash
docker compose exec rag pytest -q tests/test_retrieval.py
docker compose exec rag pytest -q tests/test_api.py
docker compose exec rag pytest -q tests/test_evaluation.py
```

## Python syntax check

```bash
docker compose exec rag python -m compileall -q /app
```

## Redmine test database safety

```bash
docker compose exec redmine bundle exec rails runner -e test \
  'db=ActiveRecord::Base.connection_db_config.database; puts db; abort("REFUSING non-test database") unless db == "redmine_test"'
```

## Redmine plugin tests

The Redmine image must include test dependencies. Its Dockerfile currently runs `bundle config unset without` followed by `bundle install`.

```bash
docker compose exec redmine bundle exec rake redmine:plugins:test \
  NAME=redmine_assistant RAILS_ENV=test
```

Current verified baseline: `44 runs, 123 assertions` for the complete plugin suite after feedback implementation. Update the number when tests change.

Targeted tests:

```bash
docker compose exec redmine bundle exec rails test \
  plugins/redmine_assistant/test/functional/redmine_assistant_controller_test.rb \
  RAILS_ENV=test

docker compose exec redmine bundle exec rails test \
  plugins/redmine_assistant/test/unit/redmine_assistant_rag_client_test.rb \
  RAILS_ENV=test
```

Ruby parse checks catch malformed syntax but not valid bare method calls such as accidental `en`:

```bash
docker compose exec redmine sh -lc '
  find plugins/redmine_assistant -type f \( -name "*.rb" -o -name "*.rake" \) -print0 |
  xargs -0 -n1 ruby -c
'
```

The actual test suite must still be executed.

## Direct API acceptance tests

```bash
curl -sS -H 'Content-Type: application/json' \
  -d '{"query":"Which automator detects plasmids?","limit":5}' \
  http://127.0.0.1:8001/api/v1/retrieve | python3 -m json.tool

curl -sS -H 'Content-Type: application/json' \
  -d '{"query":"bacon","limit":5}' \
  http://127.0.0.1:8001/api/v1/retrieve | python3 -m json.tool
```

The second response should have `result_count: 0` and `sources: []`.

## Browser acceptance checklist

1. Sign in through the normal Redmine authentication flow.
2. Confirm only authorized roles see the Assistant tab.
3. Run a documented positive query and inspect source links.
4. Run `bacon` and confirm the no-results message.
5. Submit helpful feedback.
6. Submit unhelpful feedback with a reason and comment.
7. Verify both records with the queries in [feedback.md](feedback.md).
8. Confirm a standard user cannot retrieve internal paths.
