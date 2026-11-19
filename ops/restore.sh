#!/bin/sh
# Restore trusted backup only into an independent EMPTY installation.
set -eu
cd "$(dirname "$0")/.."
[ "$#" -eq 1 ] || { printf '%s\n' 'Usage: ops/restore.sh /absolute/trusted-backup-directory' >&2; exit 1; }
case "$1" in /*) source=$1;; *) exit 1;; esac
[ -f "$source/COMPLETE" ] && [ -f "${TWIGGLE_ENV_FILE:-installation.env}" ] || { printf '%s\n' 'Read restore instructions first; configuration and COMPLETE backup required.' >&2; exit 1; }
./twiggle.sh stop web analysis exports migrate
./twiggle.sh up -d --wait db
count=$(./twiggle.sh exec -T db psql -U twiggle -d twiggle -Atqc "SELECT count(*) FROM pg_tables WHERE schemaname='public'")
[ "$count" = 0 ] || { printf '%s\n' 'REFUSED: target database is not empty; nothing restored.' >&2; exit 1; }
./twiggle.sh run --rm --no-deps migrate python ops/restore_artifacts.py --check-empty
./twiggle.sh exec -T db pg_restore -U twiggle -d twiggle --no-owner --exit-on-error < "$source/database.dump"
./twiggle.sh run --rm -T --no-deps migrate python ops/restore_artifacts.py < "$source/artifacts.tar"
./twiggle.sh up -d --wait
printf '%s\n' 'Core backup restored. Run ./twiggle.sh verify and sign in. This is not post-erasure operational recovery.'
