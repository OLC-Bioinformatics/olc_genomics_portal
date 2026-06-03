FROM redmine:6.1.2

# Install git, CA certificates, and build tools so the plugin and native gems can be installed.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*


# Install the EntraID plugin for Redmine.
RUN rm -rf /usr/src/redmine/plugins/entra_id && \
    git clone https://github.com/eea/redmine_entra_id.git /usr/src/redmine/plugins/entra_id

# Install the local CA so the corporate intercepting proxy can validate rubygems.org.
COPY GoC-GdC-Root-A.crt /usr/local/share/ca-certificates/GoC-GdC-Root-A.crt

# Copy the boot script and set permissions. The boot script will wait for the database to be ready before starting the Redmine web server.
COPY boot-redmine.sh /usr/src/redmine/boot-redmine.sh

RUN update-ca-certificates && \
    chmod +x /usr/src/redmine/boot-redmine.sh && \
    mkdir -p /home/redmine/.bundle


# Install gem dependencies in the existing Redmine workdir.
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
RUN bundle config set without 'development test' && \
    bundle install && \
    rm -rf /usr/local/bundle/cache && \
    chown -R redmine:redmine /home/redmine
