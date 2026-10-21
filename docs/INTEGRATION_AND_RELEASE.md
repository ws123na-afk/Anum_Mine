# ANUM MINE — headquarters integration batch

## Purpose

This staged implementation is intentionally larger than isolated office UI
commits. It provides a server-scoped workspace model, defensive projection,
canvas fallback, procedural GPU-rendered interior, camera controls, live
workspace management shell, authorization policies, and regression tests.

## Important source-of-truth distinctions

- PostgreSQL office registry is authoritative for building, floor, room, desk,
  assignment identity, revision and capacity.
- Current `0097_office_spaces` schema does not include room X/Z geometry.
  Room placement is therefore deterministically generated client-side by
  `layoutRoomsOnFloor`. It is **not** a persisted user-edited floor plan.
- Task-derived agent evidence does not imply human presence in the office.
  Stale, incomplete or inaccessible evidence is displayed as unavailable.
- The existing ANUM Command / Meetings / Commerce / Tyre modules retain their
  security and authorization controls. This scene only navigates to them.
- WebGL is used for the three-dimensional renderer; a clearly disclosed Canvas
  renderer is used if the graphics context cannot be initialized.

## Release wiring

1. Move the `apps/web/src/*` files into matching locations in the repo.
2. Import `HeadquartersLive` in the signed-in office tour route and provide a
   discoverable navigation entry; retain `#office-preview` for compatibility.
3. Reuse existing office permission/scope middleware and `createOfficeSpaceClient`.
4. Run the existing Node web unit tests and the included headquarters tests.
5. Run TypeScript strict checks, Vite build, Playwright Chromium tests and
   WebGL availability checks in the same Chromium engine used for deployment.
6. Verify room selection, camera orbit, zoom, and fallback in a real browser.
7. Verify separate tenant and workspace records cannot cross over after an
   in-app organization switch while requests are in flight.
8. Exercise the room management panel against a migrated staging database,
   including agent visibility, stale revision conflicts and data lifecycle.
9. Verify retained ANUM module routes in the authenticated app.
10. Confirm the approved mock visually before any claim of design parity.

## Known incomplete release gates

- The approved photographic/cinematic office reference has not been included
  in this artifact, so exact visual parity is not yet testable.
- Persistent user-edited room geometry needs a versioned backend migration,
  server-side room overlap/capacity enforcement and safe RLS CRUD endpoints.
- Actual live agent meetings require durable turn claims, model invocation,
  event/outbox delivery, governance approval, cancellation and budget charging.
- A production PA must use a permissioned tool interface rather than the old
  demonstration speech commands.
- All full-browser tests and production-app TypeScript integration are pending;
  pure Node logic tests alone do not certify deployment readiness.

## This draft is intentionally NOT a deployment instruction

Do not overwrite the current office view or deploy this staged package as if
it were a passing release. Merge only after the repository-level CI gates pass.

### Single-route application integration

The preferred signed-in route bridge is `HeadquartersEntrypoint`, not direct
mounting of `HeadquartersLive`. To preserve existing deep links, replace the
lazy `OfficeTourView` import in `main.tsx` with `HeadquartersEntrypoint` under the
same `#office-preview` hash gate. The bridge defaults to authenticated live
records and lets users opt into the old illustrative concept separately.

The mode switch must stay visually distinct so demo employees cannot be
mistaken for verified agent presence. Treat this bridge as a release candidate
only after both modes have passed Chromium accessibility and navigation tests.
