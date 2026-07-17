# Testing and Evaluation

## RAG Python tests

```bash
docker compose exec \
  -w /app \
  -e PYTHONPYCACHEPREFIX=/tmp/redmine-assistant-pycache \
  rag \
  python -m pytest -q
```

The current suite contains 92 tests.

## Redmine plugin tests

Use the repository script:

```bash
./scripts/test-redmine-assistant.sh
```

The script:

- starts a disposable Redmine container;
- installs Redmine test dependencies only in that disposable container;
- verifies that the active database is exactly `redmine_test`;
- runs controller and helper tests;
- does not run tests against production.

Current plugin baseline:

```text
24 runs
0 failures
0 errors
0 skips
```

Counts may increase as tests are added.

## Test database safety

The Redmine `database.yml` must contain a separate test configuration using:

```text
Database: redmine_test
Host: mariadb
```

Before any test execution, verify:

```bash
docker compose exec redmine \
  bash -lc '
    cd /usr/src/redmine
    RAILS_ENV=test bundle exec rails runner "
      config = ActiveRecord::Base.connection_db_config
      puts config.database
      abort \"REFUSING production database\" \
        unless config.database == \"redmine_test\"
    "
  '
```

Do not add routine plugin migrations to the test script. The unrelated Entra ID migration is not safely idempotent when its physical schema and plugin migration tracking differ.

## Retrieval evaluation

Run the full evaluation:

```bash
docker compose exec \
  -w /app \
  rag \
  python -m cli evaluate \
  --questions /app/tests/evaluation_questions.yaml \
  --top-k 5 \
  --output-json /tmp/evaluation-current.json
```

Copy the report to the host:

```bash
docker compose cp \
  rag:/tmp/evaluation-current.json \
  rag/evaluation-results/evaluation-current.json
```

## Baseline acceptance criteria

At minimum, a candidate release should maintain:

- no evaluation errors;
- no internal-document leakage;
- 100% internal-access-control check;
- no unexpected decline in Hit@1, Hit@3, or Hit@5;
- no regression in expected heading/content term coverage without explanation.

Current dense baseline:

- Hit@1: 90.4%
- Hit@3: 98.5%
- Hit@5: 100.0%
- Heading-term coverage: 91.7%
- Content-term coverage: 94.7%

## Live smoke tests

RAG API:

```bash
curl -sS \
  -X POST \
  -H 'Content-Type: application/json' \
  -d '{"query":"Which automator detects plasmids?","limit":3}' \
  http://127.0.0.1:8001/search \
  | python -m json.tool
```

Redmine UI:

1. Sign in through Entra ID.
2. Open a project with Assistant permission.
3. Open the Assistant tab.
4. Search for `Which SNVPhyl file contains pairwise SNV counts?`.
5. Confirm the top result contains `snvMatrix.tsv`.
6. Confirm source links open the hosted documentation.
7. Search for the internal merge workflow and confirm no `internal_only/` path appears.
