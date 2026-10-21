# ANUM MINE Reception & Visitor Operations

**Status:** complete front-end *demonstration module*, not a production reception booking service.

## Scope delivered
The full-screen ANUM MINE office includes an **Open Reception Operations** entry. The panel supports visitor arrival with category, purpose and priority, active queue sorting, check-in, host and room assignment, guided escort handover, completed/cancelled history, per-visitor timeline, sample arrivals, and clear/reset. An escort to an office-mapped room navigates the office camera/selection after handover starts.

## Files and ownership
- `src/lib/receptionOperations.ts`: pure typed workflow state machine, validation, deterministic queue/count selectors.
- `src/ReceptionOperations.tsx`: visitor form, queue, assignment controls, handover and timeline UI.
- `src/receptionOperations.css`: responsive view with ANUM typography tokens.
- `test/receptionOperations.test.ts`: lifecycle, invalid state transitions, sorting, cancellation and validation.
- `src/OfficeTourView.tsx`: entry point and navigation integration.

## State machine
`arrived -> checked_in -> assigned -> escorting -> completed`.
Visitors can be cancelled from any nonterminal state. Terminal records cannot be reopened. Host and destination are required on assignment. The timeline includes a UTC ISO timestamp at each transition; the UI formats local clock times. Duplicate visit IDs, empty input, and timestamps preceding previous events are rejected. Urgent open visits lead the queue.

## Explicit limitations
- No real customer, visitor identity, gate pass, access badge, calendar booking, order or shipment is recorded.
- Data exists only in memory of the rendered module, is lost when the module is closed or refreshed, and is intentionally **not** restored from localStorage.
- Employee names are placeholders; external communications and acknowledgements are not sent.
- An escort to a destination not mapped in this preview does not silently pretend to reach a physical room.
- Existing system authentication is inherited from the signed-in ANUM shell; production visitor persistence still requires a separate workspace-scoped API and explicit authorization.

## Backend integration contract (future)
Introduce server-side `visits`, `visit_events`, `visit_assignments` with tenant/workspace ID in every key, role-limited creation/transition rights, idempotency keys, monotonic revisions, server timestamps and audited event outbox. UI reducer state can become a read projection, not a source of truth. Only transition to `escorting` after a real host accepts the handover. Require visitor privacy controls, retention policy, reception roles and customer consent before real deployment.

## Manual demonstration checklist
1. Open authenticated office `#office-preview`; select **Open Reception Operations**.
2. Register a customer and supplier, optionally an urgent delivery; verify active queue ordering.
3. Check in an arrival; assign real visible sample host and destination from dropdowns.
4. Start escort and confirm mapped floor/room appears in office preview.
5. Reopen reception, create a second visitor, complete all steps and review timeline.
6. Cancel a different visitor and verify it only appears in completed history.
7. Verify reset, responsive view, keyboard focus, and clearly visible demo labels.
8. Run `pnpm --filter @anum/web check` and `pnpm --filter @anum/web build`; validate branch CI before integration.

**Integration:** draft PR against `feature/anum-mine-office-tour`, never merge into `main` without owner review.
