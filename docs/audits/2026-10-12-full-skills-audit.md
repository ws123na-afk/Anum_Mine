# Full Skills Audit (12 October 2026)

A review of the whole monorepo with every review, security, design and QA skill the owner provided, stage by stage: detect, report, fix, test, verify again. Branch `claude/anum-full-audit`, base `main` at `379b14b`.

Scope:

1. API (`services/api`): OWASP Top 10:2025 and agentic AI review, auth and OIDC, tenant isolation, input validation, rate limits, error handling, dependencies.
2. Flutter (`apps/mobile`): MASVS storage, network, auth, platform, privacy, crypto, code and resilience; a rendered design pass at phone and tablet size, light and dark, English and Arabic.
3. Desktop, Kotlin Android, contracts and infrastructure: code quality, dependencies and security.
4. Web (`apps/web`): security, performance, accessibility and test coverage only. Three other branches are editing the web UI right now (`claude/festive-ride-ghttk0`, `claude/web-freeze-fix`, `claude/web-theme-fix`), so this audit changes no web UI. Web UI findings are listed for them in [Web UI findings for the UI branches](#web-ui-findings-for-the-ui-branches).

## Summary

| Severity | Found | Fixed here | Open (owner decision or another branch) |
|---|---|---|---|
| Critical | 0 | 0 | 0 |
| High | 0 | 0 | 0 |
| Medium | 6 | 6 | 0 |
| Low | 14 | 9 | 5 |

The owner decided the four open questions on 12 October 2026 and they are implemented in this branch ([Decided by owner 12 Oct](#decided-by-owner-12-oct)).

No critical or high finding. The API already had the controls the skills look for: RS256-only OIDC validation with audience, issuer, leeway and required claims; forced row-level security with an explicit tenant context; constant-time webhook signature checks; SSRF-guarded outbound calls without redirects; bounded responses and timeouts; bandit and pip-audit clean.

## Findings and Fixes

### Medium

**M1. Quadratic work in team command parsing (API, availability).** Fixed.
`team_commands._split_parts` tested each `" to "` match with `rest[m.start() + 4:]`, which copies the rest of the string for every match; `_split_due` did the same. Parsing time grew with the square of the input: 1.6 s for 400 KB. The 2,000-character command cap (`MAX_COMMAND_CHARS`) kept production safe, but the defence-in-depth test `test_patterns_are_linear_without_the_length_cap` failed locally under load (1.07 s against a 1 s budget). Both checks now compare lengths (`m.start() + 4 < len(rest)`); 400 KB parses in 0.2 s. Covered by the existing `tests/test_team_commands.py`.

**M2. iOS keychain items could move to another device (Flutter, MASVS-STORAGE-1).** Fixed.
The session (including the OIDC refresh token) used the `flutter_secure_storage` default class, `kSecAttrAccessibleWhenUnlocked`, which an encrypted iTunes or Finder backup restores onto a different device. Session and workspace choice now use `AfterFirstUnlockThisDeviceOnly` (`deviceOnlyKeychain`). Items from earlier builds are moved on first read (delete, then add, because the class is part of the query but not of the item's identity), and sign-out deletes both classes. Android is unchanged (keystore-bound, backups off). New `test/secure_storage_test.dart`; device check added to [Flutter mobile](../mobile.md#real-device-test-checklist).

**M3. Unvalidated links handed to the OS (Flutter, MASVS-PLATFORM-1, MASVS-CODE-4).** Fixed.
The Marketing screen launched a post's `external_url` and the connect URL from the API with `launchUrl(Uri.parse(...))`. Any scheme was passed on (`intent:`, `file:`, `tel:`, `http:`) and a malformed value threw inside the button handler. Both now go through `mkHttpsLink` (absolute `https` with a host) and a helper that reports a failed launch. Every other `launchUrl` in the app already checked for `https`. Test in `test/marketing/marketing_test.dart`.

**M4. CSP allowed scripts from all of jsdelivr (web image, OWASP A05).** Fixed.
`script-src` and `connect-src` in `apps/web/nginx/default.conf.template` listed `https://cdn.jsdelivr.net`, which serves any npm package or GitHub file, so an injected script tag could load arbitrary code and read the access token in `sessionStorage`. The only thing loaded from there is the ONNX runtime loader transformers.js fetches from its own folder, so both directives now name `https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.8.1/dist/`. A new test (`test_web_csp_allows_only_the_transformers_dist_path_on_jsdelivr`) fails when that path and the locked transformers version differ, so a dependency bump cannot silently break the in-browser voice. [Security](../security.md) updated.

**M5. Floating `latest` versions in the web app (OWASP A03, supply chain).** Fixed.
`apps/web/package.json` declared `react`, `react-dom`, `vite`, `@vitejs/plugin-react`, `lucide-react`, `@types/react` and `@types/react-dom` as `latest`, and CI installs with `--frozen-lockfile=false`, so an install could pull a new major or a compromised release. They are pinned to the versions already in the lockfile (no resolution change; `pnpm install --frozen-lockfile` passes). Dependabot keeps proposing updates.

**M6. CI installs without a frozen lockfile.** Fixed (decided by owner 12 Oct): every workflow installs with `pnpm install --frozen-lockfile`.

### Low

**L1. JSON calls had no timeout (Flutter, resilience).** Fixed. `HttpApiTransport` waited forever on a hung connection, surfaced a raw `ClientException`, and threw `FormatException` on a proxy's HTML 502. It now gives up after five minutes (longer than a model call with server retries), maps transport failures to the app's existing "could not be reached" error (`ApiException` status 0), and keeps the status code of a non-JSON error page. New `test/http_api_transport_test.dart`.

**L2. Desktop webview had unused plugin permissions (least privilege).** Fixed. `opener:allow-open-url` and `global-shortcut:allow-is-registered` were granted to the webview but nothing in the web code calls them; links and the launcher shortcut run in Rust (`oidc_open_browser` accepts only `https` or a local `http` issuer). Removed; [Desktop](../desktop.md) records what the webview may call. CI's desktop job compiles the capability file (not compiled locally: disk space, see [Verification](#verification)).

**L3. Flutter component text styles dropped the brand family (design consistency).** Fixed. Button, chip, snack bar, navigation and field-label styles replace the text theme rather than merge with it, so they had no `fontFamily` or Arabic fallbacks. They now use `AnumTheme.fontFamily` and `fontFamilyFallback`. Test in `test/widgets/anum_components_test.dart`. See also O1.

**L4. Code quality (ruff, Starlette, shellcheck).** Fixed. Duplicate and unused imports in six API modules (ruff F401, F811), the deprecated `HTTP_422_UNPROCESSABLE_ENTITY` constant in `voice.py` and `autonomy_api.py` (Starlette deprecation warning in the test run), and unused loop variables in seven workflow scripts (actionlint/shellcheck SC2034).

**L5. Patch updates.** Done: SQLAlchemy 2.1.3 to 2.1.4, boto3 1.43.108 to 1.43.111, moto 5.2.3 to 5.2.4. pip-audit reports no known vulnerability before or after. FastAPI 0.143 and the OpenTelemetry 1.45.1 set are left to Dependabot (FastAPI routing changes need the route-coverage checks; the OpenTelemetry API, SDK and instrumentation versions move together).

**L6. `sprintf-js` advisory (GHSA-hp3w-g68c-fv3c, moderate).** Open. Reached only through `kokoro-js > @huggingface/transformers > onnxruntime-node > global-agent > roarr`, the Node build, which is not in the browser bundle. No patched version exists. Accept until upstream drops it.

**L7. Web access token in `sessionStorage`.** Open, architecture. Readable by any script that runs in the page; the strict CSP (now without the jsdelivr wildcard, M4) is the mitigation. Moving tokens behind an HTTP-only cookie (a backend-for-frontend) is a larger design change.

**L8. Initial web JavaScript.** Open, for the web branches. The entry loads `index` (155 kB gzip) and `i18n` (180 kB gzip) because both the English and Arabic catalogs are imported eagerly. Loading only the active locale's catalog would save about 90 kB gzip for every visitor; it changes `lib/i18n.ts`, which every view uses, so it should be done together with the branches that are editing views.

**L9. No screenshot or app-switcher protection (Flutter, MASVS-PLATFORM-3).** Fixed (decided by owner 12 Oct, O2).

**L10. Dart code is not obfuscated in release builds (MASVS-RESILIENCE).** Fixed (decided by owner 12 Oct, O3).

**L11. Flutter fonts are named but not bundled.** Fixed (decided by owner 12 Oct, O1).

**L12. Flutter plugin majors held back.** Open. `flutter_secure_storage` 11, `file_picker` 13 (11 carries an Android path-traversal fix) and `url_launcher_android` 6.3.30+ need AGP 9 built-in Kotlin, which waits on `flutter_tts` ([Flutter mobile](../mobile.md#dependencies)). Before each bump, read the plugin's `android/build.gradle`.

**L13. Sensitive values on the clipboard.** Accepted. Copying an invitation link or a webhook secret is the user's explicit action; Flutter cannot mark clipboard content as sensitive on Android 13+.

**L14. No certificate pinning.** Accepted. Pinning breaks on Let's Encrypt key rotation and the threat model does not call for it; TLS is enforced (release builds refuse plain HTTP; Android release manifest turns cleartext off; iOS has no ATS exceptions).

## Decided by Owner 12 Oct

The owner chose screen privacy and left fonts, obfuscation and the lockfile to us ("you know better"). All four are implemented in this branch:

- **O1. Fonts: bundled.** The phone app ships Inter (Latin) and Noto Sans Arabic as static instances at weights 400 to 800 (about 1.3 MB; Flutter picks a file per weight, a single variable file would synthesise bold), with the OFL texts as assets registered on the licence page. The web serves the same two faces from `public/fonts` as variable woff2 (Inter Latin 76 KB, preloaded; Noto Sans Arabic 180 KB, Arabic `unicode-range`, loaded only when Arabic shows) with `font-display: swap`, same origin, and the system fonts still in the stack. [Flutter mobile](../mobile.md#fonts), [Web localization](../web-localization.md#fonts).
- **O2. Screen privacy: screenshots blocked where possible.** Android sets `FLAG_SECURE` on the main window (screenshots, recordings and the recent-apps card are blank). iOS cannot block screenshots; the app covers its windows in the app switcher and while the screen is recorded, mirrored or shared. The secure-text-field layer trick that blanks iOS screenshots was judged too fragile (private `UITextField` layout, touch and accessibility risk) and not used. The web and desktop apps cannot block screenshots. [Flutter mobile](../mobile.md#screen-privacy), [Security](../security.md#screenshots-and-screen-recording).
- **O3. Obfuscation: on.** Android and iOS store builds use `--obfuscate --split-debug-info=build/symbols`; the release workflow uploads the symbols per build number (90 days) and CI's release app bundle uses the same flags. [Crash symbols](../mobile.md#crash-symbols) says how to symbolicate.
- **M6. Frozen lockfile: on.** Every CI install is `pnpm install --frozen-lockfile`; the lockfile matched every `package.json` after merging `main` ([Development standards](../development-standards.md)).

Still open:

- **L7. Web token storage:** keep `sessionStorage` with the strict CSP, or plan a backend-for-frontend.

## Web UI Findings for the UI Branches

Rendered with the repository's mocked API (`e2e/support/workspaceApi.ts`) in Chromium. A sweep of 23 views at 1440x900, 1366x768, 1024x768, 768x1024 and 390x844, in English and Arabic, found **no horizontal page overflow**. The axe gate (`e2e/accessibility.spec.ts`) and the localization suite passed (167 of 168 on the first local run; the one failure and a commerce page load were disk-full errors in the sandbox and passed on rerun). Layout notes for the branches that own the UI:

- **Phone header (390x844), shell:** the workspace switcher, refresh, notifications and account controls wrap onto two or three rows, about 180 px before the page title. One row with the workspace name truncated, or the workspace switcher moved into the account menu, would give that space back.
- **Phone navigation:** the primary navigation is a horizontally scrolling strip with no cue that more items follow; the active item can sit half outside the edge (Arabic, Agents). Scroll the active item into view, or add an edge fade.
- **Agents (Arabic):** the page title appears twice, as the breadcrumb heading and again as the section heading.
- **Tasks (1440x900):** the filter form (search, status, creator, dates, two checkboxes) takes about 330 px, so the first task starts near the fold; "Updates offline" and "Workspace state is current" show at the same time (with the mocked stream), which reads as a contradiction. The four summary cards place their numbers at different heights (the first card's icon is taller).
- **Home (1440x900):** the voice orb block takes about 330 px above the command box; on a laptop (1366x768) the command box is the main action and sits near the bottom of the first screen.

## Skills Used and What Each Found

| Skill | Applied to | Result |
|---|---|---|
| `full-review` | All four stages: attack surface, security, correctness, performance, quality passes | M1, L4; confirmed the API's existing controls |
| `owasp-security` | API and web against OWASP Top 10:2025, ASVS 5.0 and the agentic AI list | M4, M5, M6, L7; tool mediation and approvals already meet ASI02 and ASI09 |
| `webapp-testing` | Playwright runs against `vite preview` with the mocked API | Overflow sweep, axe and localization runs, screenshots |
| `masvs-checklist`, `mobile-threat-model` | Flutter app as a whole | Drove the storage, platform and resilience checks below |
| `secure-storage-audit` | `flutter_secure_storage`, shared preferences, Android backup settings, Kotlin `TokenVault` | M2; shared preferences hold only theme, voice and intro settings; Android backups off; Kotlin client uses Keystore AES-GCM |
| `network-security-check` | Manifests, `Info.plist`, HTTP transports | L1, L14; release cleartext off, no ATS exceptions |
| `platform-interaction-review` | Deep links, `url_launcher`, clipboard, screenshots | M3, L9, L13; the only URL scheme is the OIDC redirect (PKCE) |
| `auth-assessment` | AppAuth PKCE, refresh, sign-out | No finding; refresh token rotation and sign-out clearing already tested |
| `privacy-audit` | SDKs and data collection | No analytics SDK; Firebase Messaging only after sign-in and permission; Download/Delete my data exists |
| `crypto-review` | Dart and Kotlin crypto, API HMAC and Fernet use | No finding: `Random.secure`, AES-GCM, SHA-256 HMAC with constant-time compare |
| `code-quality-scan` | Flutter analyzer (strict), dependencies | L12; analyzer clean |
| `resilience-assessment` | Release build flags | L10 |
| `eng-code-review` | Every change in this branch | Self-review of each diff before commit |
| `eng-tech-debt` | Deprecations, held-back majors, lint debt | L4, L5, L12 |
| `eng-testing-strategy` | Coverage of changed code and web unit gaps | New tests for every fix; web unit tests cover the pure modules (`desktop.ts`, `errors.ts` through `oidc`, `admin` and `call` tests), API modules are covered by e2e |
| `eng-deploy-checklist`, `anum-release-gate` | Release evidence | Not a release: every gate in the release gate stays open for a release commit (CI run, signed artifacts, device checklist, staging smoke test) |
| `performance-audit` | Web bundle, API hot path | L8, M1 |
| `accessibility-audit`, `design-accessibility-review` | Web axe gate, Flutter semantics and text scale | Axe gate green in both languages; Flutter layout tests at 2x text exist for the screens checked |
| `frontend-design`, `high-end-visual-design`, `design-taste-frontend`, `redesign-existing-projects`, `ui-ux-pro-max` | Rendered Flutter Home and Marketing (phone 390x844, tablet 1024x768, light and dark, English and Arabic) and web screenshots | L3, O1 and the web UI notes above; Flutter screens mirror correctly in Arabic and keep contrast in both themes |
| `frontend-visual-qa` | Overflow sweep and screenshot inspection | No overflow; layout notes above |
| `anum-verify` | All changed areas | See [Verification](#verification) |

## Verification

Run locally on this branch:

| Check | Result |
|---|---|
| `python -m pytest services/api -m "not database"` | 1900 passed, 14 skipped (integration markers that need Valkey, S3, Temporal or NATS; the CI "API integration" job runs them) |
| `python -m pytest services/api -m database` (PostgreSQL 16 with pgvector on 127.0.0.1, password auth, run from the repository root) | The first run hit a full sandbox disk (`DiskFull`, an environment fault). The rerun on a fresh database: 329 passed, 1 skipped, 0 failed |
| `bandit -r services/api/anum_api services/api/migrations` | No issues |
| `pip-audit --strict services/api` | No known vulnerabilities |
| `ruff check --select E4,E7,E9,F services/api/anum_api` | Clean except `E741`/`E702` style in two modules |
| `flutter analyze --fatal-infos --fatal-warnings`, `dart format --set-exit-if-changed` | Clean |
| `flutter test` | 909 passed after the owner decisions (901 before the audit, plus the storage, transport, theme, link, font and screen-privacy tests) |
| `pnpm check` (docs, contracts, web typecheck and unit tests) and `pnpm build` | Pass |
| `pnpm install --frozen-lockfile` | Pass |
| Playwright `accessibility.spec.ts` and `localization.spec.ts` | Pass (see above) |
| `actionlint` | Clean except one SC2129 style note |
| Desktop `cargo clippy`, OSV-Scanner, gitleaks, Trivy | Left to CI: not installed in this sandbox and the disk was nearly full |
| Android and iOS builds (`FLAG_SECURE`, `PrivacyCover`, obfuscated release bundle and iOS archive) | Left to CI: no Android SDK or macOS in this sandbox. CI must prove the "Flutter" job's obfuscated release app bundle and the "Flutter iOS compile check"; the first "Release clients" run must show the symbol artifacts and a device must pass the screen-privacy checks |
| Web fonts in a browser | Chromium on `vite preview`: both faces load (200, `document.fonts` loaded), no CSP or console errors; overflow sweep of 23 views x 5 sizes x 2 languages with the new fonts: no horizontal overflow |
