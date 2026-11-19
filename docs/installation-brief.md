# Portable installation brief

Scope: self-contained Docker Compose installation of Twiggle, fresh owner chosen by operator, no shipped account, exact-origin security, persisted database and private export storage. This checkout becomes the canonical publishable source; the previous development tree and frozen demo are not modified. Runtime sources are copied by explicit path and hash-mapped locally for review. Existing repository LICENSE is immutable.

## BDD acceptance (defined before implementation)

- Given an empty installation, when setup runs, then independent random database/application secrets are privately generated, no user exists, migrations finish before web/workers start, and only a loopback web port is published.
- Given those secrets, when setup is repeated, then secrets and durable records remain unchanged.
- Given an empty installation and an operator-selected strong password, when local bootstrap runs, then exactly one ordinary workspace administrator is created atomically; concurrent or subsequent attempts fail without additional accounts.
- Given missing or synthetic provenance, when portable readiness is checked, then it fails closed rather than representing synthetic data as operational data.
- Given portable provenance and unavailable destructive-erasure authority, when core requests run, then ordinary authoring/participation/results work; operational erasure remains explicitly unavailable.
- Given an authenticated owner, when each method is authored, published and completed by a participant in a browser, then results and actual export downloads succeed.
- Given completed data, when the stack restarts, then credentials, studies and results persist.
- Given a stopped writer set, when backup is taken and restored to independent empty volumes, then exact durable records can be read back without touching the source installation.

## Boundaries

No cloud deployment, workflows, spending, native lifecycle edits, existing database connections or demo operations. Operational erasure authority is not enabled by this installer. Offline backup/restore is not a qualified post-erasure disaster recovery system. Public HTTPS requires an operator-managed TLS proxy and explicit origin configuration. Real execution evidence is local-only and bound to the final source; historical QA is not installer qualification.
