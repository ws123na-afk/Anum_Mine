# ANUM MINE starter campus: one click "Set up my campus"

Status: built in this change (API, contract, web, tests). Web and API only; no migration.

The owner should not have to create every building, floor, room and desk by hand. **Set up my campus** creates the standard campus from the owner's master list in one reviewed, click-confirmed action, the same way [agent provisioning](ANUM_MINE_AGENT_PROVISIONING.md) works. It writes ordinary office records through the builder's own code paths, so the plan, the 3D building view, the team panel and the Organisation gaps panel all read them like records the owner made by hand.

## What it creates by default

The top level is the six business buildings of the approved campus render, plus two small ones. Every name has an English and an Arabic form. Buildings are logical buildings and departments inside the current workspace: nothing here reads or writes another workspace or tenant, and nothing merges confidential data.

| Building | Floors and rooms |
|---|---|
| ANUM HQ (5 floors, 17 rooms, 39 desks) | Basement: Facilities Office, Security Operations, Parking and Logistics. Floor 1: Reception, Meeting Rooms A, B, C. Floor 2: Command Center, AI Organization, Operations Office, Assets, Suppliers. Floor 3: Executive Office, Boardroom, Strategy Room. Rooftop: Terrace, Events Hall |
| ANUM 360 Commerce (3 floors, 12 rooms, 58 desks) | E-commerce, Retail, Marketplace, Orders, Warehouse. Sales Office, Marketing Studio, Meeting Room. Customer Service, Support, Messages, Tickets |
| Tyre Pulse (2 floors, 7 rooms, 29 desks) | Workshop, Maintenance, Warehouse, Logistics. Fleet, Tyre Engineering, Inspections |
| Corporate Services (2 floors, 8 rooms, 28 desks) | HR, Finance Office, Wealth Office, Procurement. Legal Office, Compliance Office, Governance Office, Audit Room |
| Knowledge & AI (3 floors, 9 rooms, 44 desks) | AI Labs, Data, Automation. Development, Deployments, Incidents. Research Room, Intelligence Desk, Briefing Room |
| New Ventures (1 floor, 4 rooms, 14 desks) | Investments, R&D, Future Businesses, Meeting Room |
| Personal Office (1 floor, 2 rooms, 2 desks) | Personal Office, Lounge |
| Career & Employment (1 floor, 3 rooms, 6 desks) | Career Office, Employment Desk, Interview Room |

Total for an empty workspace: **8 buildings, 18 floors, 62 rooms, 220 desks**. The master list is data in `office_starter_campus.py` (`CAMPUS`); a test fails if a department of the owner's list has no room. Rooms are meeting rooms, offices, workshops, warehouses, a reception and lounges with the builder's colours and equipment kinds. Desks exist only in offices, workshops and warehouses, never above a room's capacity.

**Not staffed.** No agent is created, no desk is seated, and no presence or person is invented. A new room is empty and shows "Not staffed". Rooms with a matching department carry that `team_department` (sales, support, operations, finance, tyre engineering, marketing, technology, research, legal, compliance, security, hr, procurement), so each opens its module and the Organisation gaps panel proposes agents for the ones that have a role. The list per room is in [departments, modules and meeting rooms](ANUM_MINE_DEPARTMENTS_AND_MEETING_ROOMS.md). The owner confirms each proposal there, as before. To seat an agent at every empty desk, use [Staff my campus](ANUM_MINE_STAFF_DESKS.md): a separate, reviewed, click-confirmed step.

The Command Center of ANUM HQ is created with the room kind `command` (opens the live flow and carries the operations wall); the campus draws HQ as the executive tower, Personal Office as the villa, Tyre Pulse as the workshop and Commerce as the shop pavilion. See [building modules](ANUM_MINE_HQ_BUILDING_MODULES.md).

## Endpoints

Under `/api/v1/office/starter-campus`. PostgreSQL only (the memory backend answers 503, never fake data). Contract: `packages/contracts/src/officeStarterCampus.ts`.

- `GET /preview?buildings=hq,tyre-pulse&timezone=Asia/Dubai`: `task:read`. Every building, floor, room and desk that WILL be created (`action: "create"`) and what already exists and will be skipped (`action: "skip"` with `reason`), with exact positions in metres, plus `totals` for the selected buildings and a `payload_hash`. Nothing is stored. No `buildings` parameter means all. The time zone defaults to an existing building's zone, else `Asia/Dubai`; new buildings get it and no coordinates. `can_confirm` says whether the caller holds `workspace:manage`.
- `POST /confirm`: needs `workspace:manage`, `payload_hash` from the preview, `input_mode: "click"`, the same `buildings` list and `timezone`. A spoken or typed confirmation is refused with 403 and no registered tool calls this route. The hash binds the person, workspace, selection, time zone and the exact plan, so a change to the records, another owner or another selection gives 409 `changed since you opened it`.

## How the rules are met

| Rule | How |
|---|---|
| Idempotent | Every record has a stable id from its template key: `campus-01-hq`, `campus-01-hq-l2` (floor), `campus-01-hq-command-center` (room), `...-d3` (desk). Matching by id means a retry, a double click or a partial failure never duplicates. A confirm whose plan has nothing left to create answers `already_set_up` and writes nothing. |
| Re-run adds only what is missing | The plan is recomputed from the records every time. A deleted room comes back at its planned spot (or the next free spot) with its desks; existing records are skipped. |
| Never changes or deletes | An existing building, floor, room or desk is never updated, moved, renamed or removed, even if the owner edited it. The one exception: a room of the master list whose department is empty or `custom` gets the department of the master list (reviewed in the plan as `department_set`, audited, never replacing a department the owner chose). Desks are created only together with a new room. A room whose name is already on the floor, a floor level another floor already holds, or a floor without free space is skipped with a plain reason (`name_in_use`, `level_in_use`, `floor_unavailable`, `no_space`). |
| Atomic per building | One transaction per building under a workspace advisory lock; the building's plan is re-read under the lock and compared with the confirmed one. All of it commits or none of it. A building that cannot be saved (`failed`) or whose records moved (`changed`) leaves nothing behind; the other buildings are still built, and the answer lists each building's result and what was created. Retrying adds only the missing one. |
| Same governed code paths | Buildings and floors use `SqlOfficeSpaceStore.create`; rooms use `create_planned_room`, which locks the floor and runs the builder's `_place` checks (floor bounds, no overlap, rotation and door side values, room kinds), then inserts desks in a grid inside the room. The planner uses `office_layout` for the same geometry. |
| Tenant context | The caller's tenant, workspace and user set the RLS session. Tests run as the non-owner app role and check that another workspace's records neither count as existing nor appear. |
| Audit and events | One `office.starter_campus.building` audit record per building with `before` (existing and skipped ids with reasons) and `after` (created ids and snapshots, `payload_hash`, `input_mode: "click"`), and the existing `office.layout_changed` event per committed building. |

## Layout

Rooms are packed row by row on a floor with a 2 metre margin and 2 metre gaps; each floor's width and depth (metres) come from that packing (28 to 38 metres wide, 12 to 36 deep), so the plan and the 3D building look right and nothing overlaps. ANUM HQ is the tallest building (five levels, basement to rooftop).

3D scene: the campus scene draws one building per workspace the person belongs to, not the office buildings inside a workspace (`campusScene.ts`, density already drops above 10 workspaces). The eight starter buildings are shown one at a time in the building view through the existing building selector, so the starter campus adds no load to the campus scene.

## Web

`apps/web/src/hq-starter-campus/`. Entries: the [first-run panel](#first-run) in the scene, Office tools, **Set up my campus**, and the empty-campus state ("This workspace has no office layout yet"). The dialog reads the preview, lists every building with a checkbox (all on by default, already complete buildings disabled), a details section per building with every floor, room, skip reason and desk label, the totals, and the rules (no agents, existing kept, time zone). Changing the ticks re-reads the plan so the hash always matches what is ticked. Confirm is one click and is disabled for non-owners (with the reason), with nothing ticked, with nothing to create, and while a request runs. After the run it shows the result per building and offers Organisation gaps. States: loading, error with retry, denied (who can help), stale plan (409) with Review again, partly failed with Review what is left. English and Arabic (right to left), keyboard (focus moves in, Escape closes, focus returns), screen reader (dialog, live regions, labelled controls), responsive down to 390 pixels.

## First run

A workspace with no buildings (or one empty shell) no longer needs a hidden click. The headquarters scene shows the first-run panel (`apps/web/src/hq-first-run/`, a lazy chunk): "Create your campus", the real numbers from `GET /preview` (8 buildings, 18 floors, 62 rooms, 220 empty desks on an empty workspace), a **Preview the list** with every building and its counts, the plan fingerprint (the first 16 characters of `payload_hash`) and ONE primary button, **Create my campus**. The panel sits over the scene on a wide stage and above it, in the page flow, on a narrow one (container query at 640 px).

- The button confirms exactly the plan on screen: `POST /confirm` with that `payload_hash`, `input_mode: "click"` and the same buildings. The panel has no other way to create; typing or speaking cannot confirm, and a person without `workspace:manage` sees the button disabled with who can do it (`can_confirm` from the server) while the list stays readable.
- Without a reload: a created run reads the office registry, the campus, the live flow and the organisation gaps again, lands the camera on the campus (`flyHome`) and shows a note with the totals. Another browser creating the campus arrives through the shell's `office.layout_changed` event (the page reads again 300 ms after any event) and the panel closes by itself because the registry now has buildings.
- States: loading ("Reading the plan..."), error with Try again, denied (403, who to ask), nothing to create ("already set up"), stale plan (409: nothing is created, the new plan is read and shown, one more click creates it), a refused confirm (what failed, the button stays), a partly failed run (the buildings that were created are on the campus, the failed ones are listed with the server's reason, the panel stays; **Review what is left** re-reads the plan, then **Create the missing buildings**). The by-hand way (Manage spaces) is always one click away.
- The reviewed dialog under Office tools ("Set up my campus", building by building) is unchanged.
- Command and the executive dock: "set up my campus", "create my campus" (and the Arabic forms) show the same plan as a proposal card (`CampusProposalCard`, parser `lib/hqCampusCommand.ts`). The sentence only decides whether to show the card; the card reads the plan with the same `GET /preview` and its button is the only way to create. No server tool or route was added, so nothing a spoken or typed sentence can reach confirms anything.

Why one room used to be all anyone saw: the building camera framed the footprint with a flat 0.8 factor on its longest side. The starter floors are 28 to 38 m wide and the isometric view turns the footprint by 35 degrees, so the picture was cropped to roughly one room on a normal stage. The camera now fits every corner of the footprint (walls included) to the stage's own shape (`fitDistance` in `lib/hqCamera.ts`), and the [floor overview](ANUM_MINE_FLOOR_OVERVIEW.md) shows the whole floor on demand.

### Fixture for the browser tests

`apps/web/e2e/support/starterCampusMaster.json` is the master list with the packer's positions. Regenerate it from the repository root with:

```
cd services/api && python3 -c "
import json
from anum_api import office_starter_campus as c
out=[]
for b in c.CAMPUS:
    fl=[]
    for f in b.floors:
        pos,depth=c.pack(f.rooms,f.width)
        fl.append(dict(level=f.level,en=f.en,ar=f.ar,w=f.width,d=depth,rooms=[dict(key=r.key,en=r.en,ar=r.ar,kind=r.kind,x=pos[r.key][0],z=pos[r.key][1],w=r.width,d=r.depth,cap=r.capacity,desks=r.desks,dept=r.department) for r in f.rooms]))
    out.append(dict(number=b.number,key=b.key,en=b.en,ar=b.ar,floors=fl))
print(json.dumps(out,separators=(',',':'),ensure_ascii=False))" > ../../apps/web/e2e/support/starterCampusMaster.json
```

## Tests

API unit `tests/test_office_starter_campus.py` (master list, geometry, planner, user edits, hash); PostgreSQL as the app role `tests/test_postgres_office_starter_campus.py` (preview stores nothing, click and hash rules, full build, idempotency, partial and re-run, user edits kept, a failing building leaves nothing, two confirms at once, tenant isolation, gaps panel proposes agents); web unit `apps/web/test/hqStarterCampus.test.ts`; first run: web unit `apps/web/test/hqFirstRun.test.ts`, browser `apps/web/e2e/hq-first-run.spec.ts` (stateful mock `e2e/support/hqFirstRunApi.ts`: empty workspace, confirm fills the registry with the real master list; every state, Command card, Arabic, five viewports); browser `apps/web/e2e/hq-starter-campus.spec.ts` with the mock `e2e/support/hqStarterCampusApi.ts` (main path, selection, permissions, every error state, empty state, keyboard, Arabic, five viewports).

## Not built yet

- The time zone is taken from the workspace's buildings (else Asia/Dubai); there is no field to pick it in the dialog and no coordinates are set.
- The master list is code (data in `office_starter_campus.py`), not editable by the owner in the app.
- A deleted room is recreated on the next run; there is no "do not recreate" memory.
- Room names are stored in English with the Arabic name in `name_ar`; building and floor names have no Arabic column, so the Arabic names show in the setup dialog only.
- Other open browsers learn of the new records through the existing `office.layout_changed` event only where they already listen to it.
