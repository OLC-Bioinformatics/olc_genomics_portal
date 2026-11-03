# Project Restriction

The Redmine Documentation Assistant is enabled only for one configured Redmine project. The project-menu entry is hidden in every other project, and direct Assistant routes for other projects return HTTP 404.

## Configuration

Set the stable Redmine project identifier used in project URLs, not the numeric database ID:

```yaml
services:
  redmine:
    environment:
      - REDMINE_ASSISTANT_PROJECT_IDENTIFIER=cfia
```

For a project URL such as:

```text
/projects/cfia
```

the identifier is `cfia`.

The plugin fails closed when the setting is missing or empty:

- the Assistant project-menu entry is not displayed;
- index, search, and feedback routes return HTTP 404.

Changing the Compose value requires container recreation. Plugin code changes also require rebuilding the Redmine image:

```bash
docker compose up -d --build --force-recreate redmine
```

## Verify the running configuration

Print the live identifier:

```bash
docker compose exec redmine \
  sh -lc '    printf "Assistant project identifier: %s\n" \
      "${REDMINE_ASSISTANT_PROJECT_IDENTIFIER:-<missing>}"  '
```

Resolve it against the Redmine database:

```bash
docker compose exec redmine \
  bundle exec rails runner '
    identifier = ENV.fetch(
      "REDMINE_ASSISTANT_PROJECT_IDENTIFIER",
      ""
    ).strip

    project = Project.find_by(identifier: identifier)

    abort(
      "Configured Assistant project does not exist: #{identifier}"
    ) unless project

    puts "Assistant project: #{project.name} (#{project.identifier})"
  '
```

## Security enforcement

The restriction is enforced at two layers:

1. `redmine/plugins/redmine_assistant/init.rb` hides the project-menu entry unless the project identifier matches.
2. `RedmineAssistantController#require_assistant_project` returns HTTP 404 before index, search, or feedback processing for every other project.

Project restriction is separate from authorization. A successful request must satisfy all of the following:

1. The user is authenticated.
2. The requested project exists.
3. The project identifier matches `REDMINE_ASSISTANT_PROJECT_IDENTIFIER`.
4. The user has `view_redmine_assistant` for that project.
5. Internal documentation additionally requires `view_internal_assistant_documentation`.

The browser cannot override the configured project or access context.

## Routes

The routes exist globally, but the controller permits them only for the configured project:

```text
GET  /projects/:project_id/redmine_assistant
POST /projects/:project_id/redmine_assistant/search
POST /projects/:project_id/redmine_assistant/feedback
```

## Browser acceptance checks

1. Open the configured project and confirm the Assistant tab appears for an authorized user.
2. Open another project and confirm the Assistant tab does not appear.
3. Navigate directly to another project's Assistant URL and confirm HTTP 404.
4. Navigate directly to the configured project's Assistant URL and confirm it works for an authorized user.
5. Remove `view_redmine_assistant` from the user's role and confirm access is denied in the configured project.
6. Confirm search and feedback submission work in the configured project.

## Automated tests

Verify the test database before running Redmine tests:

```bash
docker compose exec redmine \
  bundle exec rails runner -e test '
    db = ActiveRecord::Base.connection_db_config.database
    puts db
    abort("REFUSING non-test database") unless db == "redmine_test"
  '
```

Run controller tests:

```bash
docker compose exec redmine \
  bundle exec rails test \
  plugins/redmine_assistant/test/functional/redmine_assistant_controller_test.rb \
  RAILS_ENV=test
```

Current verified controller baseline:

```text
22 runs, 86 assertions, 0 failures, 0 errors, 0 skips
```

Run the complete plugin suite:

```bash
docker compose exec redmine \
  bundle exec rake redmine:plugins:test \
  NAME=redmine_assistant \
  RAILS_ENV=test
```

Current verified plugin baseline:

```text
48 runs, 131 assertions, 0 failures, 0 errors, 0 skips
```

The controller tests temporarily assign `ENV["REDMINE_ASSISTANT_PROJECT_IDENTIFIER"]` and restore the original value during teardown. Do not narrowly mock `ENV.fetch`. Redmine may evaluate the project-menu condition while rendering a response, and an unexpected Mocha invocation against `ENV` can print the complete process environment.

## Troubleshooting

### Assistant appears in the wrong project

Check the live environment value and recreate Redmine. A simple restart does not apply changed Compose environment values:

```bash
docker compose up -d --build --force-recreate redmine
```

### Assistant does not appear in the configured project

Verify that:

1. the configured value is the project identifier, not its numeric ID;
2. the identifier matches the project URL exactly;
3. the project exists;
4. the user's project role has `view_redmine_assistant`;
5. Redmine was rebuilt after changes to `init.rb`;
6. Redmine was recreated after changing the environment value.

### Direct Assistant URL returns 404

A 404 is expected when the project does not exist, the project is not the configured Assistant project, or the configured identifier is missing or empty.

### Tests print the complete environment

Avoid mocking `ENV.fetch`. Save the original `ENV[...]` value, assign the controlled test value, and restore or delete it during teardown. Treat credentials printed in test output as exposed and rotate them through the approved secret-management process.
