# ANUM MINE headquarters: deployment checklist and rollback

For the owner. Plain steps. Nothing here deploys by itself; production is gated on green checks.

## The switch

The office route (`#office-preview`) and its sidebar link ("ANUM MINE") are on by default. The build flag
`VITE_ANUM_HQ=off` hides both. A stale `#office-preview` link then opens the normal app. The flag is read
when the web image is built, so changing it means rebuilding the web image.

- Oracle VM: set `ANUM_HQ=off` in the stack's environment file, then rebuild and restart the web service
  (`infra/oracle/compose.yaml` passes it as `VITE_ANUM_HQ`). Back on: remove the line or set `ANUM_HQ=on`.
- Render: add the build argument `VITE_ANUM_HQ` with `off` (the Dockerfile accepts it, default `on`).
- Code: `apps/web/src/lib/headquartersFlag.ts`; unit test `apps/web/test/headquartersFlag.test.ts`.

The flag hides the screen only. The APIs stay available to signed-in users with the right permissions; that
is intended, they are tenant-isolated and permission-checked.

## Before staging

1. `main` CI is green (the Browser end-to-end shards 2 and 6 were red on 22 October: PR 217 fixes it).
   Check `gh run list --branch main --limit 3`.
2. Pending HQ branches are merged in this order: data, visual, meetings, executive, readiness. Each
   migration is renumbered at merge (data 0101, meetings 0102, executive 0103) and every reference is
   updated. Revision ids stay at 32 characters or fewer.
3. `pnpm docs:check`, `pnpm --filter @anum/web check`, the API unit and database jobs and the Browser
   end-to-end job are green on the merge commit.
4. Security review (`docs/ANUM_MINE_HQ_SECURITY_REVIEW.md`): no open High; S-02 and S-03 decided by you.
5. Performance (`docs/ANUM_MINE_HQ_PERFORMANCE_BUDGET.md`): the budget test passes; you read the numbers on
   the machine you use.

## Staging rollout

1. Deploy with the flag on to staging only (Oracle staging stack). Production keeps `ANUM_HQ=off` until the
   last step.
2. Run the migrations (`migrate` service). They are expand-only: new tables and columns, nothing dropped.
3. Run `infra/oracle/smoke.sh` and confirm the API health and headers are fine.
4. Sign in as a test user in a test organisation. Open ANUM MINE. Check: rooms load or the empty state
   offers "Manage office layout"; create a building, a floor, a room and a desk; the desk list shows
   "No current activity evidence" until an agent has a task; switch light and dark; open Live workstations.
5. Run the load test (`docs/ANUM_MINE_HQ_LOAD_TESTING.md`) at 21 and at 200. Keep the numbers.
6. Turn the staging flag off, rebuild, confirm the link is gone (this is the rollback rehearsal; record the
   time it took).
7. Turn it on again. Ask two other people to open it on a phone and a laptop.
8. Sign-off: write the date, the people and the numbers at the bottom of this file.
9. Production: deploy with `ANUM_HQ=off`, run migrations, then switch on after a short check.

## Rollback

Cheapest first.

1. **Hide the screen** (a few minutes): set the flag off and rebuild the web image (see "The switch").
   Nothing else is touched. Do this first when the office misbehaves for users.
2. **Roll the API image back** to the previous tag. Migrations 0097 to 0103 are expand-only, so the older
   API runs against the newer schema; the extra tables are simply unused. `helm rollback` and image
   rollback never reverse migrations.
3. **Roll the web image back** to the previous tag if the whole web build is bad.
4. **Remove data** only when you must (for example a test organisation): delete the test workspace in
   the organisation settings; the tenant lifecycle purge removes its office and meeting rows. Never
   downgrade a migration on a shared database.
5. After any rollback: `infra/oracle/smoke.sh`, then open the shell and one older page.

Rollback triggers: a failed health check after deploy, cross-tenant data seen by anyone (stop, hide the
screen, then investigate), the API pool exhausted while office tabs are open, or repeated 5xx from the
office routes.

## What is not proven yet

- No real run on the Oracle VM: load numbers, memory and the rollback rehearsal are still to do.
- The database RLS test (`services/api/tests/test_postgres_hq_rls.py`) has only run in CI, if at all.
- Phones: the room inspector is hidden below 768 px (H-04), so the office is not yet a phone surface.
- Arabic: the office text is not translated (H-06).

## Sign-off

Not signed off. (Date, people, numbers go here after staging.)
