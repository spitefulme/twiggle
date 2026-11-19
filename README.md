# Twiggle

Self-hosted, mobile-first card sorting and tree testing. Researchers author and publish immutable study versions; participants join through a capability link without an account; researchers inspect results and download evidence.

## Install

Prerequisites: **Git**, **Docker Engine/Desktop with Docker Compose v2**, a POSIX shell, Internet access for the initial image/package build, and an available loopback port. On Windows use **WSL2** with Docker Desktop integration, not Command Prompt/PowerShell. No host Python, Node, PostgreSQL or paid service is needed.

```sh
git clone https://github.com/spitefulme/twiggle.git
cd twiggle

# Build and start Twiggle, its database and background workers.
./setup.sh

# First installation only: choose your administrator username and password.
# This creates your workspace; no default account or password is provided.
./twiggle.sh owner

# Recommended: check that the database, workers and download service are ready.
# Run this again after upgrades or when troubleshooting; it is not a full test suite.
./twiggle.sh verify
```

Open **http://127.0.0.1:8080** on the machine running Docker. There is **no default account**: the owner command prompts locally for your chosen username and strong password. For a remote server, localhost is the server; use an SSH tunnel or an explicitly configured TLS proxy, not an implicitly public listener.

Setup generates private, independent database/application credentials in ignored `installation.env` (0600). It starts an independent PostgreSQL database, applies migrations, then starts Gunicorn, one analysis worker and one export worker. Database and private export artifacts persist in project-scoped Docker volumes. Setup does not modify host services or install global packages.

For a different port/project/origin, set these **before the first setup**:

```sh
TWIGGLE_PORT=8091 TWIGGLE_ORIGIN=http://127.0.0.1:8091 TWIGGLE_PROJECT=twiggle-personal ./setup.sh
```

Repeated setup preserves existing secrets and data. Read [installation and security](docs/installation.md), [operations: upgrade/backup/restore](docs/operations.md), [limitations](docs/limitations.md), and [local verification](docs/acceptance.md).

**Important:** the portable core supports authoring, participation, analysis and exports. Operational destructive erasure/retention authority and post-erasure recovery are **not configured or qualified**; erasure fails closed. Do not collect data subject to deletion/retention obligations until that limitation is resolved. This is not a claim of general production, regulatory or cross-platform qualification.

## Source and license

This repository is the canonical publishable Twiggle source. The installer does not depend on a developer checkout, a pre-existing database, private framework, historical report or frozen demo. [Source boundaries](docs/source-boundaries.md) explain the transition and test-evidence limits.

The existing repository [LICENSE](LICENSE) is preserved unchanged. Dependency licenses remain their own; see [third-party notices](third_party/README.md). No GitHub Actions workflows or cloud deployment are installed.
