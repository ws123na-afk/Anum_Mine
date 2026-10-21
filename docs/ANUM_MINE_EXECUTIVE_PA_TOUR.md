# Executive PA Guided Tour Module

## Scope delivered
A complete **interactive demonstration module** in the authenticated ANUM MINE `#office-preview` route. Click **CEO Guided Tour** on the floor panel to start. The module provides a CEO welcome, ordered department route per building, floor transitions, selected-room camera/PA location updates, adjustable browser narration, pause/resume, direct room redirect, completion, restart, progress meter, and chronological visit history. It uses the same room and staff *example* descriptions as the office, avoiding contradictory floor mapping.

## Files
- `apps/web/src/lib/guidedExecutiveTour.ts`: typed deterministic state machine, validation, timeline, progress and room descriptions
- `apps/web/src/GuidedExecutiveTour.tsx`: narrated interactive executive tour view and controls
- `apps/web/src/guidedExecutiveTour.css`: responsive control panel using ANUM typography tokens
- `apps/web/test/guidedExecutiveTour.test.ts`: lifecycle, interruptions, redirects, invalid transitions, reset, truthful demonstration claims
- `apps/web/src/OfficeTourView.tsx`: route integration, mapping actual preview floor/room selection from itinerary
- `apps/web/src/lib/officeTourNavigation.ts`: pre-existing separate command parser

## Interaction contract
`ready -> touring -> paused -> touring -> finished`, with direct `redirect` from any non-finished state and `reset` to new site itinerary. Each state-changing event has a validated timestamp, and completed room IDs contribute to progress. The room list comes from the selected building; changing buildings remounts the tour so status and history do not leak between sites.

PA speech is **off by default**. The user can enable browser speech synthesis. Speech output is cancelled on pause, close and component cleanup. The room selection changes the floor and the existing 2.5D office PA marker. Narration explicitly describes staff as *example roles*, not live agent presence.

## Acceptance demonstration
1. Sign into ANUM, open `#office-preview`, choose **CEO Guided Tour**.
2. Begin at reception; take next destinations, check floor transitions and room selection.
3. Pause mid-visit and resume. Jump to the executive office before completing other rooms.
4. Toggle narration off and on; verify microphone is not implicitly activated.
5. Open visit history, complete the last room and restart.
6. Switch business; confirm routes and history restart for the newly selected building.
7. Use a keyboard and mobile-sized view. Confirm no animation is forced when reduced motion is enabled.
8. Run `pnpm --filter @anum/web check`, web build and E2E manual walkthrough before integrating.

## Remaining engineering
This module is a **complete tour and narration UI**, not a finished real-world 3D experience. Physical avatar rigging, collision-safe pathfinding, actual hallway/elevator animation, cinematic follow camera, native low-latency voice integration, and live employee status need other approved workstreams. Do not claim those as already working. No production data writes, agent execution or cross-workspace access are added.

**Integration policy:** Draft PR #187 targets `feature/anum-mine-office-tour`. Do not merge into main without owner review.
