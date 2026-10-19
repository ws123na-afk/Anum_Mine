# Production Readiness Gates

ANUM keeps local product verification separate from infrastructure and credential gates. A passing unit suite is not a production-readiness claim.

## Local Product Gates

- `pnpm check` validates documentation and TypeScript contracts.
- `pnpm build` creates the production web bundle.
- `pnpm test:e2e` runs the task journey, navigation, accessibility-name, and responsive-overflow checks at desktop and mobile viewports. CI runs the suite in six parallel shards ("Browser end-to-end (shard n/6)", each with the fixed ports 4173, 4174 and 4175); the required "Browser end-to-end" check passes only when every shard passed, and merges the shards' reports into the artifact `playwright-report`. A failed shard also uploads `playwright-traces-<n>` (the traces and screenshots of failed tests from `apps/web/test-results/`); both are kept 7 days. Open the report with `pnpm --filter @anum/web exec playwright show-report <unzipped folder>` and a trace at trace.playwright.dev. A failed "Flutter mobile" test step likewise uploads `flutter-test-results` (the JSON results from `flutter test --file-reporter`). Uploading never changes a job's result.
- `python -m pytest services/api -m "not database"` validates infrastructure-independent API behavior.
- `pnpm check:desktop` compiles the Rust desktop shell after Rust and Windows C++ prerequisites are available.
- `pnpm check:android` validates Android unit tests after JDK 17, Gradle, and the Android SDK are available.

## Infrastructure Gates

The PostgreSQL, Keycloak, NATS, Temporal, Valkey, and S3 object storage adapters require their real services. CI must apply migrations, run database-marked tests, exercise authentication, publish and consume a durable event, resume a workflow after worker restart, verify distributed locking, and round-trip a file through object storage.

## Credential Gates

Production acceptance requires model-provider credentials, OIDC client secrets, notification-provider credentials, deployment configuration, artifact signing identities, and marketplace signing keys. Secrets must come from the deployment secret store and must never be committed or exposed to the browser.

## Release Evidence

Each release records the web build, API test results, browser test report, Android APK/AAB checksum, signed desktop installer checksum, migration revision, infrastructure smoke-test result, and the environment in which each check ran. Deployments also record the "Helm deploy (kind)" CI result, the deployed image digests, the Helm revision before and after, `helm test` output and the revision to roll back to ([Deployment](deployment.md)). A skipped gate remains open and must be reported explicitly.
