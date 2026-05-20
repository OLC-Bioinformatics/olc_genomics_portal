FROM redmine:latest

# Install git, CA certificates, and build tools so the plugin and native gems can be installed.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install the EntraID plugin for Redmine.
RUN rm -rf /usr/src/redmine/plugins/entra_id && \
    git clone https://github.com/eea/redmine_entra_id.git /usr/src/redmine/plugins/entra_id

# Install the local CA so the corporate intercepting proxy can validate rubygems.org.
COPY GoC-GdC-Root-A.crt /usr/local/share/ca-certificates/GoC-GdC-Root-A.crt
COPY boot-redmine.sh /usr/src/redmine/boot-redmine.sh
COPY config/initializers/smtp_retry.rb /usr/src/redmine/config/initializers/smtp_retry.rb
RUN update-ca-certificates && \
    chmod +x /usr/src/redmine/boot-redmine.sh && \
    mkdir -p /home/redmine/.bundle && \
    chown -R redmine:redmine /home/redmine

# Install gem dependencies.
WORKDIR /usr/src/redmine
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
RUN bundle config set without 'development test' && \
    bundle install \
    && rm -rf /usr/local/bundle/cache
