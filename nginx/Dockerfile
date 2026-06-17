FROM nginx:stable

# Remove default nginx config
RUN rm /etc/nginx/nginx.conf

# Copy custom nginx config
COPY nginx.conf /etc/nginx/nginx.conf

# Ensure sites-enabled directory exists
RUN mkdir -p /etc/nginx/sites-enabled

# Copy site configurations
COPY sites-enabled/ /etc/nginx/sites-enabled/

# Create required directories for logs
RUN mkdir -p /var/log/nginx && \
    chown -R nginx:nginx /var/log/nginx

# Forward request logs to Docker log collector
RUN ln -sf /dev/stdout /var/log/nginx/access.log && \
    ln -sf /dev/stderr /var/log/nginx/error.log

# Remove default site configuration to prevent conflicts
RUN rm /etc/nginx/conf.d/default.conf

EXPOSE 80 443