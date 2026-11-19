# Local installation acceptance

## Fast service verification

```sh
./twiggle.sh verify
```

Requires a bootstrapped running installation. Checks live non-synthetic provenance, governance availability, analysis heartbeat, export singleton ownership, private artifact storage and guarded download transport. It does not manufacture study data or qualify a deployment by itself.

## Executable portable scenarios

Run only on an explicitly disposable installation (the test runner creates/drops its own `test_twiggle` database, using database-administrator credentials in the short-lived test service, never in web/workers):

```sh
docker compose --env-file installation.env -f compose.yaml -f compose.test.yaml run --rm --no-deps tests
```

The suite covers private/idempotent configuration, origin-injection refusal, fresh owner creation, weak-password refusal, bootstrap concurrency/second-owner refusal, synthetic/unknown provenance rejection, unavailable erasure authority, private exact-byte artifact restore and no partial writes for a late traversal entry, and portable shell/exact Host behavior. A failed-first record is kept locally before new bootstrap/configuration behavior and concrete defects were implemented; no historical suite counts are imported.

## Real browser journeys

The optional QA image uses the existing pinned Playwright dependency lock. It needs additional image download/disk space. Browser execution is Linux-only here (`--network host`); it is not the installation runtime.

```sh
docker build -f ops/Dockerfile.browser -t twiggle-browser-qa .
```

Prepare a disposable owner's username `installation-owner` with a randomly chosen test-only strong password held in a **private** file outside the repository. Set a private output directory. Never run this on real participant data: it authors new studies and participant responses.

```sh
docker run --rm --network host --memory=1g --cpus=1   -e TWIGGLE_TEST_ORIGIN=http://127.0.0.1:8080   -v /absolute/private/test-password:/run/owner-password:ro   -v /absolute/private/acceptance-output:/evidence   twiggle-browser-qa
```

Six cases: two security-boundary cases plus card sorting and tree testing in Chromium desktop and phone contexts. Each journey signs in through the UI, authors/publishes through the UI, admits a participant in a separate browser context, completes/submits through actual controls, runs analysis, filters retained evidence and verifies a real CSV/JSON browser download. Reports/screenshots/traces stay private outside Git; traces can contain the test password and capability links. Treat the entire directory as sensitive.

## Complete installation drill

In addition to passing executable scenarios: verify zero accounts/studies before owner bootstrap; no externally published database port; non-superuser/no-createdb application role; migrations before web/workers; refusal of second analysis/export workers; generated-secret preservation across repeated setup; exact durable record/byte identities across restart; wrong-origin, unauthenticated and CSRF rejection; offline backup catalogue/content readback; restore into different empty volumes/network/port; restore refusal over a nonempty target; retained owner login and real export from the restored instance; then delete only the disposable restore resources and recheck the original.

Bind receipts to the exact Git/source-map and Docker image IDs. Count unique cases, not exploratory retries. Local receipt paths, credentials and fixture identifiers must not be committed. Broader core regression, all-browser/mobile gesture coverage, internet-facing deployment, erasure and operational recovery are not implied by these scenarios.
