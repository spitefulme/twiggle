#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username postgres --dbname postgres --set=app_password="$TWIGGLE_APP_PASSWORD" <<'SQL'
CREATE ROLE twiggle LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
CREATE DATABASE twiggle OWNER twiggle;
SQL
