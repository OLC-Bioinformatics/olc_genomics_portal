#!/usr/bin/env bash
set -euo pipefail

cd /usr/src/redmine
export RAILS_ENV=production

if [ -n "${REDMINE_DEFAULT_FROM:-}" ]; then
  echo "=== Ensuring Redmine default_from is set to ${REDMINE_DEFAULT_FROM} ==="
  ruby -ryaml -e '
    path = "/usr/src/redmine/config/configuration.yml"
    cfg = File.exist?(path) ? YAML.load_file(path) : {}
    cfg ||= {}
    cfg["production"] ||= {}
    cfg["production"]["default_from"] = ENV["REDMINE_DEFAULT_FROM"]
    File.write(path, YAML.dump(cfg))
  '
fi

if [ -n "${EMAIL_HOST_USER:-}" ] || [ -n "${EMAIL_HOST_PASSWORD:-}" ]; then
  echo "=== Updating Redmine SMTP credentials from env ==="
  ruby -ryaml -e '
    path = "/usr/src/redmine/config/configuration.yml"
    cfg = File.exist?(path) ? YAML.load_file(path) : {}
    cfg ||= {}
    cfg["production"] ||= {}
    cfg["production"]["email_delivery"] ||= {}
    cfg["production"]["email_delivery"]["smtp_settings"] ||= {}
    cfg["production"]["email_delivery"]["smtp_settings"]["user_name"] = ENV["EMAIL_HOST_USER"] if ENV["EMAIL_HOST_USER"]
    cfg["production"]["email_delivery"]["smtp_settings"]["password"] = ENV["EMAIL_HOST_PASSWORD"] if ENV["EMAIL_HOST_PASSWORD"]
    File.write(path, YAML.dump(cfg))
  '
fi

# Wait for MariaDB to accept connections.
echo "=== Waiting for MariaDB on mariadb:3306 ==="
while ! bash -c '</dev/tcp/mariadb/3306' >/dev/null 2>&1; do
  echo "MariaDB is unavailable - sleeping"
  sleep 2
  continue
done

echo "=== Running Redmine database migrations ==="
bundle exec rake db:migrate

echo "=== Running Redmine plugin migrations ==="
bundle exec rake redmine:plugins:migrate

# Load default data if the database appears empty or core objects are missing.
if bundle exec rails runner "puts User.count" | grep -q '^0$'; then
  echo "=== Loading Redmine default data ==="
  bundle exec rake redmine:load_default_data RAILS_ENV=production REDMINE_LANG=en <<'EOL'
en
EOL
else
  echo "=== Checking for missing core Redmine data ==="
  if ! bundle exec rails runner "exit(1) if User.count == 0 || Tracker.count == 0 || IssueStatus.count == 0 || Role.count == 0"; then
    echo "=== Core Redmine data missing; loading default data ==="
    bundle exec rake redmine:load_default_data RAILS_ENV=production REDMINE_LANG=en <<'EOL'
en
EOL
  else
    echo "=== Core Redmine data present; skipping default data load ==="
  fi
fi

echo "=== Ensuring Redmine REST API is enabled ==="
bundle exec rails runner "Setting['rest_api_enabled'] = '1'"

echo "=== Applying fallback password migration ==="
bundle exec rails runner - <<'RUBY'
fallback_password = 'Migrated123!'
users = User.where(admin: false).where('passwd_changed_on IS NULL OR last_login_on IS NULL')
puts "Setting fallback password for #{users.count} user(s)"
users.find_each do |u|
  u.password = fallback_password
  u.password_confirmation = fallback_password
  u.must_change_passwd = true
  u.save!(validate: false)
end
RUBY

# Launch the Redmine web server.
echo "=== Starting Redmine web server ==="
bundle exec rails server -b 0.0.0.0 -p 3000
