# Upgrade, backup and restore

Always operate from the intended checkout and private `installation.env`. `./twiggle.sh ps` shows that project's services. `./twiggle.sh logs --tail 100` may contain study identifiers; keep diagnostics private. Avoid exporting full container environments.

- Stop without losing data: `./twiggle.sh down`.
- Restart existing services: `./twiggle.sh restart`; then `./twiggle.sh verify`.
- **Destructive:** `./twiggle.sh down -v` deletes this project's data volumes. Never use it as a routine update or restore step.

## Upgrade

1. Record the current Git SHA (`git rev-parse HEAD`) and back up while writes are stopped as below.
2. Fetch/review the intended release, then check out its exact commit/tag. Preserve `installation.env` and the existing Compose project name. Review migration/release notes first; there is not yet a supported automatic cross-version downgrade contract.
3. Stop writers: `./twiggle.sh stop web analysis exports`.
4. Run `./setup.sh`. The migration service finishes before replacement application services start. This installation always rebuilds from the checked-out source with pinned Python dependency versions/hashes. Base images use explicit version tags but are not digest-pinned; package mirror/base OS changes can affect rebuilt images. Record image IDs for controlled deployments.
5. `./twiggle.sh verify`, sign in, open a retained study/results view and check a real export download. Setup success alone is not acceptance. Do not claim historical tests qualify a changed image.

A failed migration/build leaves the backup intact; investigate instead of deleting volumes or editing applied migrations. For rollback, restore the matching backup to a separate empty installation with the matching source, validate it, then deliberately switch traffic. Restoring only an old image against a newer schema is unsupported.

## Offline core backup

```sh
./ops/backup.sh /absolute/private/new-backup-directory
```

The destination must not already exist. The command creates a 0700 directory under umask 077, stops web/analysis/exports, dumps PostgreSQL in custom format, validates its catalogue with `pg_restore --list`, archives private artifacts and copies the secret configuration, writes `COMPLETE` only after success, and restarts writers via a trap. A successful backup pauses work; plan the interruption. It starts all three writers afterward, so use it only when your intention is a running installation. A failed backup may leave a partial directory without `COMPLETE`; retain it privately for diagnosis, never restore it as complete. SIGKILL/host failure cannot run shell traps; use `./twiggle.sh start web analysis exports` after diagnosing the failure.

Store the complete directory off-machine under encryption/access control. It contains credentials, participant data, capabilities and export evidence. No encryption, rotation, remote upload or retention policy is implied. Backups include the application key: losing/changing it can invalidate authenticated data/session capabilities. If secrets are compromised, restoring a backup is not credential rotation.

## Isolated restore (trusted backups only)

Use the **same source version** initially and a **new project name** and unused loopback port. Do not run setup/migrations/bootstrap before the restore: the database must be empty.

1. Make a private copy of the backup's `installation.env` outside the checkout (0600). Keep its secret values. Change only `COMPOSE_PROJECT_NAME`, `TWIGGLE_PORT`, and `TWIGGLE_ORIGIN` to the new instance. Never reuse the live project's name/volumes. Example name: `twiggle-restore-check`; example origin/port: `http://127.0.0.1:8092` / `8092`.
2. In the source checkout, set `export TWIGGLE_ENV_FILE=/absolute/private/restore.env`. This selects an independent config for `twiggle.sh` and backup/restore helpers; setup always uses the checkout's ordinary `installation.env` and must not be used here.
3. `./twiggle.sh build migrate` (build only, no schema creation).
4. `./ops/restore.sh /absolute/private/complete-backup-directory`.
5. `./twiggle.sh verify`. Sign in with the retained owner password, inspect study/result identities/counts, and verify actual exports. Keep restored traffic private while validating.
6. When finished with a disposable restore drill only: `./twiggle.sh down -v`, then `unset TWIGGLE_ENV_FILE`. Confirm the original installation is still healthy before removing any backup.

Restore first stops the target writers, creates its independent database service, refuses any public schema tables or existing export files, restores the database without `--clean`, restores only bounded unique flat regular artifacts (no links/path traversal), then migrates/starts the target. Invalid input fails closed, but a database/IO failure may leave a partially restored target: keep it stopped, inspect it, and recreate **only the explicitly disposable target volumes** before retrying. Restore is not transactional across PostgreSQL and files. Do not feed it an untrusted archive. The artifact archive is bounded to 260 MiB/4096 members and 256 MiB data.

This is ordinary offline core backup/readback, **not qualified post-erasure operational recovery**. The external rollback-independent erasure authority is intentionally absent. Never use these instructions to assert deletion guarantees, re-enable deleted participant records, or bypass a governance quarantine.
