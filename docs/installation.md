# Installation and security

## Supported installation model

One Compose project owns one database, one private export volume and a private Docker network. Only web publishes a port, bound to `127.0.0.1`; PostgreSQL is not published. The core has no mandatory external authentication or analytics. Allow image/package downloads on initial build. Later starts can use the built images. This is not a fully offline distribution.

Tested locally: Linux ARM64, Docker Engine 29.1.3, Compose 2.37.1, Python 3.11.14 images, PostgreSQL 16.10, and Chromium desktop/phone contexts in the pinned Playwright image. AMD64 Linux, macOS Docker Desktop and Windows WSL2 are intended installation targets but have not been exercised in this qualification. Native Windows shells, rootless Docker, IPv6 origins and alternate container engines are not qualified. Allow several GB of disk for builds/browser QA and roughly 3 GB RAM for the core stack plus the host/container engine; resource ceilings are in Compose, not a measured minimum specification.

Run the README commands as a user authorized to use Docker. Docker access is effectively host-administrator access. Keep the checkout and its parent directory private from untrusted local users. POSIX shell, `id`, `mkdir`, `cp` and Git/Docker are the only setup host tools; application/runtime dependencies install inside images. Docker Desktop must be running. Compose v1 `docker-compose` is unsupported.

## Initialization

1. Choose an unused port and a unique Compose project name if another Twiggle installation exists. Defaults are port 8080/project `twiggle`; reuse means operating the same volumes, not a second installation.
2. `./setup.sh` creates ignored `installation.env` exclusively with 0600 permissions. Application key, application database password and database administrator password are independent CSPRNG values. Repeating setup validates private ownership/permissions and never rewrites that file. Do not commit it, print `docker compose config`, or paste Docker inspect environments into support logs.
3. PostgreSQL initializes an unprivileged `twiggle` login and its owned database. Web/workers have only the application password; they are not database superusers and cannot create roles or databases. Administrative credentials remain in the private config and database container.
4. Compose waits for PostgreSQL, completes Django migrations, then starts web/workers. App containers run UID/GID 10001, read-only root filesystems, bounded tmpfs/memory/CPU/PIDs, dropped capabilities and no-new-privileges. Private export storage is owned by that UID with 0700 directory/0600 artifacts. Docker volume access is sensitive.
5. Until an owner is created, `/health/live` succeeds but `/health/ready` fails closed. Run `./twiggle.sh owner`. Password input is hidden and validated (14+ characters, non-common/non-numeric). Bootstrap serializes through a database advisory lock, refuses any pre-existing account/workspace/provenance, creates one ordinary workspace administrator (not Django staff/superuser), and permanently closes. There is no public registration/bootstrap HTTP endpoint or shipped account.
6. Open the configured origin, sign in, create a study and publish a participant link. Card-sort authoring starts with editable example cards/groups; those are not stored until you create/save a study. Participant session identifiers are pseudonymous, not proof of anonymity or one response per person. Treat participant and download links as secrets.

For unattended owner creation, an explicitly supported local interface reads a password from private stdin, never an argument:
`./twiggle.sh exec -T web python manage.py bootstrap_owner --username YOUR_NAME --password-stdin`.
Do not place a real password in shell history. The normal interactive interface is preferred.

## Origin / HTTPS

`TWIGGLE_ORIGIN` is one exact lowercase ASCII HTTP(S) origin, without path, trailing slash, query, credentials or wildcard. Omit explicit default ports. HTTP is allowed only for `127.0.0.1` or `localhost`. For Django application routes, Host including port must match exactly (health checks are exempt); unsafe cross-origin requests are denied and Django CSRF checks remain active. Immutable public `/static/` assets are served by the WSGI allowlist before Django middleware and are exempt from the application Host check; private exports and application routes are not. Cookies are HTTP-only/Strict and use Secure + `__Host-` prefix when HTTPS is configured. Local HTTP uses a distinct non-prefixed cookie. No CORS wildcard or forwarded-header trust is enabled.

For a remote audience, set `TWIGGLE_ORIGIN=https://your.example` before initialization (or edit only that value privately and recreate the app services later). Configure your own TLS proxy on the host to forward to the loopback port and **preserve the configured Host and Origin**. The installer does not issue certificates, open firewall ports, or validate your proxy. Do not expose the raw HTTP port. Configure proxy request/rate limits, authentication-abuse controls, trusted client-IP handling and HTTPS redirect/HSTS there. Forwarded IPs are deliberately ignored: the app's existing participant admission throttling otherwise groups proxy users by proxy IP. Public reverse-proxy deployment and load/security testing remain operator responsibilities; this delivery tests loopback only.

## Recovery and operational boundaries

`./twiggle.sh verify` checks non-synthetic portable provenance, database governance availability, fresh analysis heartbeat, held export singleton lock, private artifact storage and the guarded download worker. It is a service preflight, not a complete browser test or recovery certification. Readiness explicitly reports operational erasure/recovery unavailable. Do not enable synthetic environment flags or point the operational erasure authority at an arbitrary directory.

Forgotten owner password can be changed locally using Django's interactive command: `./twiggle.sh exec web python manage.py changepassword YOUR_NAME`. This is host-operator authority, not public password recovery. No email reset, email delivery, MFA, automated account provisioning or multiple-owner bootstrap is provided. Existing workspace membership authorization remains in place.
