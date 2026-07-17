#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

docker compose run --rm redmine \
  bash -lc '
    set -euo pipefail

    cd /usr/src/redmine

    echo "=== Enabling Redmine test dependencies ==="
    bundle config unset without
    bundle install --quiet

    echo "=== Verifying test database isolation ==="
    RAILS_ENV=test bundle exec rails runner "
      config = ActiveRecord::Base.connection_db_config

      puts \"Environment: #{Rails.env}\"
      puts \"Database: #{config.database}\"
      puts \"Host: #{config.host}\"

      abort \"REFUSING: test environment uses production database\" \
        unless config.database == \"redmine_test\"
    "

    echo "=== Running Redmine Assistant controller tests ==="
    RAILS_ENV=test bundle exec rails test \
      plugins/redmine_assistant/test/functional/redmine_assistant_controller_test.rb
  '
