# Running the portal tests

The portal test database is a dedicated PostgreSQL 16 container. It is not
connected to the production database, and its port is not published to the
host or other Compose projects.

From `olc_genomics_portal`, start the database:

```sh
docker compose -p foodport-test -f docker-compose-test.yml up -d postgres
```

Run the default test suite with coverage:

```sh
docker compose -p foodport-test -f docker-compose-test.yml run --build --rm test-runner
```

The runner uses `config.settings.test`, creates its own `test_foodport_test`
database, and reports coverage when the tests pass. Use `--build` on a clean
checkout if the test image has not been built. Browser-driven Selenium tests
are skipped in this default suite and run in the separate browser overlay.

Run the Primer Finder browser tests with the opt-in Selenium Firefox service:

```sh
docker compose -p foodport-browser-test \
  -f docker-compose-test.yml \
  -f docker-compose-browser-test.yml \
  run --build --use-aliases --rm test-runner
```

The first run downloads the pinned Selenium Firefox image. The browser tests
exercise dynamic target creation, result-detail toggling, and confirmation or
cancellation of project deletion. They use the isolated test database and
leave the regular test command unchanged. Stop the browser and database
containers afterward with:

```sh
docker compose -p foodport-browser-test \
  -f docker-compose-test.yml \
  -f docker-compose-browser-test.yml \
  down
```

The test database volume is preserved.

`docker compose -p foodport-test -f docker-compose-test.yml down` stops the
container but preserves the isolated test database volume.

## Continuous integration

The **Foodport-CI** Azure Pipeline (ID `1064`) runs on pushes and pull requests
for `main`, `dev`, and `foodport`. It checks dependency locks, Django system
checks, critical Flake8 errors, translations, and the Django suite; then runs
Selenium and Nginx static-asset tests. It builds all deployment images,
smoke-tests Batch startup and its health endpoint, verifies non-root portal
image access, and runs the RAG tests. These tests use isolated test services
and mocks; they do not call production Azure resources.

GitHub rulesets require this check for PRs targeting `dev` and `main`. The CI
status badge in the repository README tracks the latest `dev` run.

The CI job does not run `makemigrations --check`: the current legacy model
state produces existing migration proposals. Resolve that baseline drift
before adding the command as a blocking check.
