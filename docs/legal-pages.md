# Public pages: legal documents, Trust and status

The web host serves these pages without sign-in:

| URL | What it is | Source |
|---|---|---|
| `https://<WEB>/legal/terms` | Terms of Service (DRAFT) | [docs/legal/terms-of-service.md](legal/terms-of-service.md) |
| `https://<WEB>/legal/privacy` | Privacy Policy (DRAFT), Saudi PDPL and UAE PDPL terms, real data flows and retention | [docs/legal/privacy-policy.md](legal/privacy-policy.md) |
| `https://<WEB>/legal/dpa` | Data Processing Addendum (DRAFT) with the security measures annex | [docs/legal/data-processing-addendum.md](legal/data-processing-addendum.md) |
| `https://<WEB>/legal/aup` | Acceptable Use Policy (DRAFT) | [docs/legal/acceptable-use-policy.md](legal/acceptable-use-policy.md) |
| `https://<WEB>/legal/subprocessors` | Subprocessor list (DRAFT) | [docs/legal/subprocessors.md](legal/subprocessors.md) |
| `https://<WEB>/trust` | Trust page: security facts and the vulnerability disclosure contact | `apps/web/src/PublicSite.tsx`, messages `publicSite.{en,ar}.ts` |
| `https://<WEB>/status` | Live status of the web app, API and sign-in, checked from the browser | `apps/web/src/PublicSite.tsx` |
| `https://<WEB>/privacy` (and `/privacy/`) | The short Privacy Policy published on 13 October 2026 for TikTok and the app stores | `apps/web/public/policies/privacy.html` |
| `https://<WEB>/terms` (and `/terms/`) | The short Terms of Service published on 13 October 2026 | `apps/web/public/policies/terms.html` |

For the owner's deployment `<WEB>` is `app.anum360os.com`.

## The legal documents in docs/legal

The five documents in `docs/legal/` are drafts written from what the code and deployment configuration actually do (checked 18 October 2026): the subprocessors that code and config use, retention as implemented, export and deletion as built. Each starts with "DRAFT, owner and a lawyer must review before publishing". They make no legal conclusions and claim no certification. Every fact only the owner can supply is a `{{PLACEHOLDER}}`, for example `{{LEGAL_ENTITY_NAME}}`, `{{REGISTERED_ADDRESS}}`, `{{TRADE_LICENCE_NUMBER}}`, `{{PRIVACY_CONTACT_EMAIL}}`, `{{GOVERNING_LAW}}` and the hosting regions.

The web app renders them from the same files: `apps/web/src/lib/legalDocs.ts` imports the Markdown at build time (`?raw`), `src/lib/publicSite.ts` drops the source's DRAFT quote, reads the title and "Last updated" line, rewrites links between the documents to their routes, and `PublicSite.tsx` renders the result with the safe Markdown reader (`src/lib/markdown.ts`: text nodes only, `http(s)` and `mailto` links only). Placeholders are highlighted. The documents are English only; the Arabic interface says so above the text. The web image copies `docs/legal` for this (`apps/web/Dockerfile`, `Dockerfile.dockerignore`), and Render rebuilds the web service when `docs/legal/**` changes.

### Publishing a document

Each page shows a **Draft, not yet published** banner until the build marks it published:

1. The owner and a lawyer approve the text and replace every `{{PLACEHOLDER}}` in the file.
2. Set `VITE_ANUM_LEGAL_PUBLISHED` for the web build to the published ids, comma-separated (`terms,privacy,dpa,aup,subprocessors`) or `all`: `ANUM_LEGAL_PUBLISHED` in `infra/oracle/.env` (passed as a build argument by `infra/oracle/compose.yaml`), the `VITE_ANUM_LEGAL_PUBLISHED` variable of the Render web service, or `--build-arg` for a plain `docker build`.
3. Rebuild the web image (`docker compose up -d --build web` on the VM).

A document marked published that still contains a placeholder keeps the banner and says how many facts are missing (`legalState` returns `blocked`), so a half-filled document cannot be shown as final.

### ANUM_PRIVACY_POLICY_URL

When `ANUM_PRIVACY_POLICY_URL` is empty the API reports `https://<web origin>/legal/privacy` as the privacy policy (`sign_in_options.effective_privacy_policy_url`, used by `GET /api/v1/auth/sign-in-options` and `GET /api/v1/privacy`). The web origin is the first public `https` origin in `ANUM_CORS_ORIGINS`; `http`, `localhost`, `*.localhost` and loopback origins are skipped, so local and test runs still report none. An explicit value always wins. `keycloak-2fa.sh sign-up` still requires an explicit value, so Keycloak's own sign-up page is only turned on against a policy the owner chose to publish.

### Cookies

ANUM's web app and API set no cookies, and the web app loads no analytics, advertising or tracking scripts (checked: no `document.cookie`, `Set-Cookie` or `set_cookie` in `apps/web/src`, `services/api/anum_api`, the nginx template or the Caddyfile). Keycloak sets only the cookies its sign-in needs. The web app keeps preferences in `localStorage` and the sign-in session in `sessionStorage`. So there is **no cookie banner**; the Privacy Policy states this under "Cookies and browser storage". Add a notice before adding any non-essential cookie or analytics.

## Trust page

`/trust` states only facts the code and configuration support, each with its source:

| Statement | Where it is true |
|---|---|
| Tenant isolation by forced PostgreSQL row-level security | [Multi-tenancy](multi-tenancy.md), `tests/test_postgres_rls.py` |
| HTTPS with HSTS; credentials encrypted with a Fernet key outside the database; B2 server-side encryption; off-site backups encrypted with rclone crypt | [Security](security.md), [Deploying on Oracle](deploy-oracle.md#backups) |
| Second factor required for every password sign-in; passkeys accepted | [Identity](identity.md#two-factor-sign-in) |
| Append-only audit log, searchable and exportable by owners and organization administrators | [Governance and scale](governance-and-scale.md#audit-log) |
| AI tool calls mediated, approvals by policy, opt-outs enforced, content not used for training | [Approvals and risk](approvals-and-risk.md), [Privacy](privacy.md#contact-suppression) |
| Nightly backups, 7 days on the server, off-site copies expire after 35 days, restore drill | [Deploying on Oracle](deploy-oracle.md#backups) |
| Self-service export and deletion | [Privacy](privacy.md) |
| CI scanners: pip-audit, pnpm audit, OSV-Scanner, cargo audit, Bandit, CodeQL, Flutter analyze, Clippy, gitleaks, Trivy | [Security](security.md#scanning-in-ci) |
| No third-party certification and no external penetration test yet | [Production plan](production-plan.md) (G7 open) |
| Vulnerability reports to the address in [SECURITY.md](../SECURITY.md) | `SECURITY.md`, `PublicSite.tsx` `SECURITY_CONTACT` (a unit test keeps them equal) |

Keep the page and these sources in step: when a measure changes, change the message in `publicSite.en.ts` and `publicSite.ar.ts` (add keys, never rename) in the same change. The backup statement describes the configured mechanism; while the owner's off-site backups fail ([pending work](pending-work.md)), fix them before relying on the page for a sale.

## Status page

`/status` checks, from the visitor's browser, every minute while the tab is visible and on **Check again**:

| Component | Check | States |
|---|---|---|
| Web app | The page itself loaded from the web host. | Operational |
| API | `GET <VITE_ANUM_API_URL>/health` (the API's health route; it is exempt from rate limiting), 8 second timeout, expects `{"status": "ok"}`. | Operational with the round trip, Problem (HTTP error or another body), Not reachable (no answer, CORS refusal or timeout) |
| Sign-in | `GET <VITE_ANUM_OIDC_ISSUER>/.well-known/openid-configuration`, expects the configured issuer. | Operational, Problem, Not reachable, or Not set up when the build has no issuer |

The headline is **All systems operational**, **Some systems are having problems**, or **ANUM's API cannot be reached** with what that means for the visitor; an offline device gets its own message. The API has no public readiness route that reports the database; Keycloak's own health routes are blocked at Caddy. The page therefore shows what anyone can check from outside, keeps no history and uses no paid service. For alerts and history the operator runs an uptime monitor ([Monitoring the Oracle VM](monitoring-oracle.md)). Requests go to origins the web CSP already allows (`ANUM_CSP_CONNECT_SRC` lists the API and Keycloak), without credentials.

## The short pages at /privacy and /terms

They are plain HTML with inline CSS only (no scripts, nothing from another origin), dark by default and light when the device prefers it, readable at 390 px. `apps/web/nginx/default.conf.template` has exact-match `location = /privacy`, `/privacy/`, `/terms` and `/terms/` blocks that serve `public/policies/*.html` before the single-page fallback, each repeating the security headers. The files moved from `public/legal/` to `public/policies/` so that Vite's preview server, which maps `/legal/privacy` to a `legal/privacy.html` file, cannot shadow the new routes. `apps/web/test/legalPages.test.ts` guards them. The web sign-in screens link to them. Once the drafts in `docs/legal` are approved, point TikTok, the stores and these links at the `/legal/*` pages and retire the short ones.

## Tests

- `apps/web/test/publicSite.test.ts`: routes, the published flag, the DRAFT banner logic, every document's DRAFT line, links and placeholders, no certification claims, facts checked against `settings.py`, `calls/api.py`, `.env.example` and `deploy-oracle.md`, the disclosure contact, and the status checks (healthy, HTTP error, wrong body, network error, timeout).
- `apps/web/e2e/public-site.spec.ts`: each route renders without sign-in and makes no API call besides `/health`, the DRAFT banner, links between documents, the Trust facts, status operational, unreachable with recovery, HTTP error and offline, Arabic right to left, the skip link, axe (English, Arabic, light theme) and no horizontal overflow at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844.
- `services/api/tests/test_sign_in_options.py`: the privacy policy default.

## Not done yet

- The legal text is English only.
- The status page has no incident history or subscriptions.
- Deleting a whole organization is an operator task without a documented procedure; the DPA leaves `{{TENANT_DELETION_PROCEDURE}}` for it.
