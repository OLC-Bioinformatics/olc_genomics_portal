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
docker compose -p foodport-test -f docker-compose-test.yml run --rm test-runner
```

The runner uses `config.settings.test`, creates its own `test_foodport_test`
database, and reports coverage when the tests pass. Browser-driven integration
tests are excluded from the default suite.

Run the Primer Finder browser tests with the opt-in Selenium Firefox service:

```sh
docker compose -p foodport-browser-test \
  -f docker-compose-test.yml \
  -f docker-compose-browser-test.yml \
  run --use-aliases --rm test-runner
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
