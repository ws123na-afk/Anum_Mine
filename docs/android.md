# Android

The shipping ANUM Android app is the Flutter client in `apps/mobile` ([Flutter mobile](mobile.md)), applicationId `com.anum.app`. The Kotlin client in `apps/android` described below is frozen.

## Status: Frozen

Decision (Stage 6 of the [Production plan](production-plan.md)): Flutter is the shipping app on Android and iOS, because it already has what the Kotlin client lacks:

- Keycloak sign-in (authorization code + PKCE, refresh, RP-initiated sign-out); the Kotlin client has no OIDC sign-in yet ([Identity and sign-in](identity.md#client-sign-in)).
- The wake-by-name voice assistant with English and Arabic recognition, transcript review and spoken confirmation.
- Real-data Home, Tasks, Approvals, Automations, Resources and Governance screens, Arabic RTL, 200 percent text scaling and tablet layouts, covered by widget tests.
- One codebase for Android and iOS.

What frozen means for `apps/android`:

- It keeps building in CI (the "Android client" job: unit tests and a debug APK) so it does not rot, and security fixes are allowed.
- No new features, no release signing and no Play listing. New Android work goes into `apps/mobile`.
- Its applicationId `com.anum.mobile` and the `anum-android` Keycloak client stay reserved so the two apps never collide on a device or in the realm.
- Native capabilities Flutter cannot reach (for example widgets or a share target) are added to `apps/mobile/android` as platform code, not to this client. Retiring it completely (deleting the module, the CI job and the realm client) is a separate decision once the Flutter app is on the Play internal track.

## Original Design

The Kotlin app was planned as a native client for task creation, approvals, notifications, voice entry, and lightweight review, sharing backend contracts rather than duplicating runtime logic. The sections below record that design; the same product role and rules now apply to the Flutter app.

## Product Role

Android should focus on fast capture, mobile approvals, task status, notifications, and voice. It does not need to match every desktop or web workflow at launch. The mobile experience should make it easy to supervise agents while away from the main workspace.

## Architecture

The Android app should use OIDC sign-in, REST APIs for resources, realtime or push channels for task updates, and typed generated clients when API contracts stabilize. Sensitive local state should use Android secure storage and avoid long-lived raw provider tokens.

## Permissions

Permissions should be requested only when a feature needs them. Microphone, notifications, files, contacts, calendar, and accessibility-style permissions must be explicitly justified in product UX and backend policy.

## Offline Behavior

Early Android versions can support offline drafts and queued user messages. Agent execution should remain server-side until a separate local runtime design is approved.

## Implemented

The Kotlin and Compose client provides task capture and execution, task status, approval decisions, voice capture with just-in-time microphone permission, typed REST access, and encrypted token storage. CI runs unit tests and assembles a debug APK.

## Release Gate

The Kotlin client is not released (see [Status: Frozen](#status-frozen)). Local APK validation requires JDK 17, Gradle, the Android SDK, and ADB. The Android release gate for the shipping app (signing, Play App Signing, device testing) is in [Flutter mobile](mobile.md#release-builds).
