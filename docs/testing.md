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
database, and reports coverage when the tests pass. Browser-driven
`test_integration.py` modules are excluded from the default suite; run them
separately after installing and configuring their browser driver.

`docker compose -p foodport-test -f docker-compose-test.yml down` stops the
container but preserves the isolated test database volume.
