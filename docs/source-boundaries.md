# Source and publication boundary

The public Twiggle checkout is the canonical publishable source for this installer. Runtime modules were imported by an explicit `researchloop/**` source/static/template path allowlist plus `manage.py` and the existing Python dependency locks. No private databases, environment files, generated reports, screenshots, sessions, native AI-DLC harness/vendor directories, host virtualenvs, node_modules, developer history or approved internal design/audit documents are part of this repository.

The previous developer source and frozen live demo are not rewritten or operated by setup. No installer references their paths, database ports or private state. A local-only original-source SHA-256 map and a final publication map are retained for independent review, outside Git. Subsequent app work should use this canonical checkout; reconciling any still-running frozen demo is a separate, explicit operation, not an automatic installer side effect. Do not maintain unaccounted parallel feature versions.

Intentional import differences:

- Default settings now select independent operational core settings; developer-only synthetic settings are excluded. No historical synthetic provenance is relabelled. Fresh bootstrap records `portable-core`, `synthetic_only=False`; synthetic/unknown records cannot pass portable readiness or be bootstrapped over.
- Portable bootstrap, exact-origin middleware, immutable allowlisted static WSGI serving, worker orchestration and additive provenance choice/migration are new.
- The researcher's title/header/sign-in copy identifies Twiggle/private hosting rather than a synthetic developer workspace. This is not a broad visual redesign.
- A browser acceptance failure exposed the fixed-group add form shrinking on input-to-button focus, causing pointer activation to miss. The one CSS selector now keeps its width stable for the entire focused form. The failing browser assertion is retained in local evidence; method/domain rules are unchanged.
- Python dependency versions/hashes are retained; the developer-local `--find-links` path is removed. The original Node lock is retained for pinned optional browser QA, not used by the running app. Third-party Unicode and installed Python license notices remain separate from the existing user-owned root license.

The new portable tests are an additional, explicitly scoped executable inventory. The old developer qualification suites/reports are not copied/relabelled as installer evidence, and no tests, timeouts or inventories in that tree are edited. Historical aggregate pass counts do **not** qualify this changed installer. Broader application regression/design review remains distinct from portable-install acceptance and is not represented as rerun here.
