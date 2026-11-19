#!/bin/sh
set -eu
cd "$(dirname "$0")"
command -v docker >/dev/null || { printf '%s\n' 'Install Docker Engine/Desktop first.' >&2; exit 1; }
docker compose version >/dev/null
umask 077
mkdir .setup-lock 2>/dev/null || { printf '%s\n' 'Another setup is running (or remove a stale .setup-lock after checking).' >&2; exit 1; }
trap 'rmdir .setup-lock' EXIT HUP INT TERM
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/work" -w /work python:3.11.14-slim-bookworm python ops/configure.py installation.env "${TWIGGLE_ORIGIN:-http://127.0.0.1:8080}" "${TWIGGLE_PORT:-8080}" "${TWIGGLE_PROJECT:-twiggle}"
docker compose --env-file installation.env build
docker compose --env-file installation.env up -d --wait --wait-timeout 180
printf '%s\n' 'Twiggle started. Create your first owner: ./twiggle.sh owner' 'Read docs/installation.md before collecting real participant data.'
