#!/bin/sh
set -eu
cd "$(dirname "$0")"
config=${TWIGGLE_ENV_FILE:-installation.env}
[ -f "$config" ] || { printf '%s\n' 'Run ./setup.sh first.' >&2; exit 1; }
case "${1:-}" in
 owner) shift; exec docker compose --env-file "$config" exec web python manage.py bootstrap_owner "$@" ;;
 verify) exec docker compose --env-file "$config" exec -T web python manage.py portable_verify ;;
 *) exec docker compose --env-file "$config" "$@" ;;
esac
