#!/bin/sh
# Trusted offline backup, never overwrites an existing destination.
set -eu
cd "$(dirname "$0")/.."
[ "$#" -eq 1 ] || { printf '%s\n' 'Usage: ops/backup.sh /absolute/private/new-backup-directory' >&2; exit 1; }
case "$1" in /*) destination=$1;; *) printf '%s\n' 'Use an absolute backup destination' >&2; exit 1;; esac
umask 077
mkdir -m 700 "$destination"
./twiggle.sh stop web analysis exports
trap './twiggle.sh start web analysis exports >/dev/null' EXIT HUP INT TERM
./twiggle.sh exec -T db pg_dump -U twiggle -d twiggle -Fc > "$destination/database.dump"
./twiggle.sh exec -T db pg_restore --list < "$destination/database.dump" > "$destination/database.list"
./twiggle.sh run --rm --no-deps --entrypoint tar migrate -C /artifacts -cf - . > "$destination/artifacts.tar"
cp "${TWIGGLE_ENV_FILE:-installation.env}" "$destination/installation.env"
printf '%s\n' 'Twiggle offline core backup v1' > "$destination/COMPLETE"
printf '%s\n' 'Backup complete; keep the entire directory private and off this machine.'
