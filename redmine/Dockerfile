FROM redmine:6.1.2

# Install git, CA certificates, and build tools so the plugin and native gems can be installed.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates build-essential \
    && rm -rf /var/lib/apt/lists/*


# Install the EntraID plugin for Redmine.
RUN git init /usr/src/redmine/plugins/entra_id && \
    git -C /usr/src/redmine/plugins/entra_id remote add origin \
        https://github.com/eea/redmine_entra_id.git && \
    git -C /usr/src/redmine/plugins/entra_id fetch --depth 1 origin \
        5549f4c2f7be8ed60659321c9e63927989382591 && \
    git -C /usr/src/redmine/plugins/entra_id checkout --detach FETCH_HEAD

# Install the local Redmine documentation assistant plugin.
COPY plugins/redmine_assistant \
    /usr/src/redmine/plugins/redmine_assistant

# Install the local CA so the corporate intercepting proxy can validate rubygems.org.
COPY GoC-GdC-Root-A.crt /usr/local/share/ca-certificates/GoC-GdC-Root-A.crt

# Copy the boot script and set permissions. The boot script will wait for the database to be ready before starting the Redmine web server.
COPY boot-redmine.sh /usr/src/redmine/boot-redmine.sh

RUN update-ca-certificates && \
    chmod +x /usr/src/redmine/boot-redmine.sh && \
    mkdir -p /home/redmine/.bundle && \
    chown -R redmine:redmine \
        /home/redmine \
        /usr/src/redmine/plugins/redmine_assistant


# Install gem dependencies in the existing Redmine workdir.
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
RUN bundle config unset without && \
    bundle install && \
    rm -rf /usr/local/bundle/cache && \
    chown -R redmine:redmine /home/redmine
