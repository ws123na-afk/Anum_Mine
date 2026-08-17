# ANUM Android

A native Kotlin/Jetpack Compose client (see [docs/android.md](../../docs/android.md)) covering this "Now" scope's two priorities — fast task capture and mobile approvals — sharing the same backend contracts and OIDC identity as `apps/web` rather than duplicating runtime logic.

## What's here

- `app/src/main/kotlin/dev/anum/android/`
  - `auth/` — Authorization Code + PKCE against Keycloak via [AppAuth](https://github.com/openid/AppAuth-Android) (the audited-library choice mirrors `apps/web/src/lib/auth.ts`'s use of `keycloak-js` — see that file's comment for the reasoning). `TokenStore` persists AppAuth's `AuthState` in `EncryptedSharedPreferences`; `JwtClaims` decodes the ID token's `tenant_id`/`workspace_id`/`roles` claims the same way `auth.ts`'s `getTenantContext()` does.
  - `data/model/` — Kotlin data classes mirroring `services/api/anum_api/schemas.py` / `packages/contracts`' wire shapes exactly (snake_case `@SerialName`s matching the API's actual JSON).
  - `data/api/` — a Retrofit interface (`AnumApiService`) against the same endpoints `apps/web/src/lib/api.ts` calls, an `AuthInterceptor` attaching a fresh Bearer token per request (refreshing via AppAuth when needed), and `NetworkModule` wiring it all together (no DI framework — see that file's note on why).
  - `ui/` — Compose screens: `LoginScreen`, and a two-tab (`Tasks`, `Approvals`) shell (`AnumNavHost`) after sign-in. Each screen has a `ViewModel` exposing a `StateFlow<UiState>`.
- Standard Gradle multi-module layout (`settings.gradle.kts`, root `build.gradle.kts`, `gradle/libs.versions.toml` version catalog, `app/build.gradle.kts`).

## Build status: **not build-verified in this environment**

Unlike `apps/desktop` (a real, compiled-and-run Tauri shell — see that app's README), this Android app's Gradle build could not be run to completion here:

- No Android SDK (platform tools, `aapt2`, `android.jar`) is installed, and it cannot be installed — `dl.google.com` and the parts of `maven.google.com` that serve the Android Gradle Plugin itself are network-blocked in this sandbox (confirmed directly: `gradle tasks` fails at "Plugin `com.android.application` was not found... Searched in: Google, MavenRepo, Gradle Central" — i.e. it gets exactly as far as attempting that fetch, which is the expected/predicted failure, not a surprise).
- What **is** confirmed: `settings.gradle.kts`, the root `build.gradle.kts`, and `gradle/libs.versions.toml` are syntactically valid Kotlin DSL/TOML — Gradle parsed all of them without error and proceeded to real plugin resolution before failing on the network block above.
- The Kotlin source itself has **not** been compiled or type-checked here (no `android.jar` means Compose/Activity/AppAuth-dependent code can't be checked without a real SDK). It was written carefully against each library's actual current API (AppAuth's `AuthorizationService`/`AuthState`, Retrofit's `kotlinx-serialization` converter, Compose Navigation's `NavHost`), but treat it as a strong starting point to build and fix up on a machine with real SDK access, not as verified-working code.

To actually build this, on a machine with normal internet access: install Android Studio (which provisions the SDK for you) or the standalone `cmdline-tools` + `sdkmanager`, accept the SDK licenses, then `./gradlew assembleDebug` from this directory (add a Gradle wrapper first — `gradle wrapper` — since none is checked in here for the same reason none of this has been build-verified).

## Before this can sign in against the local dev realm

`infra/docker/keycloak/anum-realm.json` only defines an `anum-web` client today. This app's debug build points at a not-yet-created `anum-android` public client (see `app/build.gradle.kts`'s `KEYCLOAK_CLIENT_ID`) with redirect URI `dev.anum.android://oauth2redirect` — add that client to the realm export (same `tenant_id`/`workspace_id`/`roles` protocol mappers as `anum-web`, PKCE `S256` enforced, no client secret) before attempting a real sign-in.

## Not built (explicitly out of scope for this pass)

- **Push notifications** — needs a backend push-delivery integration (FCM registration, a server-side sender) that doesn't exist yet; documented as a gap rather than half-wired.
- **Voice entry** — `docs/voice.md` explicitly defers voice as a later surface ("Voice should become a natural ANUM surface, but it should not be the first core dependency"); this app has zero voice code, matching that guidance.
- **Offline drafts** — `docs/android.md` lists this as a "Later" item; not attempted here.
