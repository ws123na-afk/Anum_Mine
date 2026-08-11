# Android

The ANUM Android app should be built in Kotlin as a native client for task creation, approvals, notifications, voice entry, and lightweight review. It should share backend contracts rather than duplicate runtime logic.

## Product Role

Android should focus on fast capture, mobile approvals, task status, notifications, and voice. It does not need to match every desktop or web workflow at launch. The mobile experience should make it easy to supervise agents while away from the main workspace.

## Architecture

The Android app should use OIDC sign-in, REST APIs for resources, realtime or push channels for task updates, and typed generated clients when API contracts stabilize. Sensitive local state should use Android secure storage and avoid long-lived raw provider tokens.

## Permissions

Permissions should be requested only when a feature needs them. Microphone, notifications, files, contacts, calendar, and accessibility-style permissions must be explicitly justified in product UX and backend policy.

## Offline Behavior

Early Android versions can support offline drafts and queued user messages. Agent execution should remain server-side until a separate local runtime design is approved.

## Now

Document Kotlin as the Android direction and design APIs that mobile can consume cleanly.

## Later

Add push notifications, mobile approvals, voice capture, widgets, share-sheet ingestion, offline drafts, and optional local context tools.