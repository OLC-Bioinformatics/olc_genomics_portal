#!/bin/bash
psql -U postgres -c "CREATE USER $DB_USER PASSWORD '$DB_PASS'"
psql -U postgres -c "ALTER ROLE $DB_USER SUPERUSER"
psql -U postgres -c "CREATE DATABASE $DB_NAME OWNER $DB_USER"
