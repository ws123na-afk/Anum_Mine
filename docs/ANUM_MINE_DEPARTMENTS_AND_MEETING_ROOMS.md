# ANUM MINE: departments with real modules, agent roles for them, and meetings tied to rooms

Status: built in this change (API, one migration, contracts, web, tests). Web and API only. Closes owner decision O3 for Marketing, Technology, Research and Legal (and adds Compliance, Security, HR and Procurement because the starter campus already builds rooms for them).

## 1. Departments

A room stores one department (`office_rooms.team_department`). The storable set is now:

`sales, support, operations, finance, tyre_engineering, marketing, technology, research, legal, compliance, security, hr, procurement, executive, custom`

Old values stay valid. The set lives in four places that a test keeps equal: the Python enum `team_models.Department`, `office_layout.TEAM_DEPARTMENTS` with the API literal `TeamDepartment`, the database check constraint `ck_office_room_department` (widened by migration `0108_departments_meeting_room`), and the contract `OFFICE_TEAM_DEPARTMENTS` (`packages/contracts/src/officeBuilder.ts`, also `team.ts` `Department`). The web builder list (`hqBuilder/labels.ts`) is checked against the contract list in `test/hqRoomModule.test.ts`.

People (team profiles) and agents can carry the same departments, so the room's team count and the Organisation gaps panel work for the new ones. The five built in department assistants (sales, support, operations, finance, tyre engineering) are unchanged; no assistant is invented for the new departments.

### Department to module

Every module is a view that exists in the shell (`lib/shell.ts`). Where nothing exists the room says so; there is no dead button.

| Department | Opens | Why this and not another |
|---|---|---|
| `marketing` | Marketing (`#marketing`) | Calendar, drafts, Brevo campaigns |
| `technology` | Developer mode (`#developer`) | GitHub projects and coding requests. Owners use it; anyone else sees what it is and who can use it |
| `research` | Knowledge Base collections (`#knowledge`) | The workspace's saved collections and memories |
| `legal` | Documents (`#files`) | The files library where contracts live |
| `compliance` | Governance (`#governance`) | Policy packs, roles, approval rules |
| `security` | Audit log (`#audit`) | Who did what, when |
| `hr` | Members and roles (`#members`) | People, invitations, roles. This is access management, not a payroll or recruiting module |
| `executive` | Executive desk (`#executive`) | Added with migration `0116_room_dept_executive` (expand only, no backfill). The starter campus Executive Office carries it; a room's building kind no longer decides this |
| `procurement` | No module connected | There is no procurement or supplier module. The room says so and offers Organisation gaps |
| `custom`, unknown | No module connected | By design |
| `sales`, `support`, `operations`, `finance`, `tyre_engineering` | unchanged: `#whatsapp`, `#inbox`, live flow dock, `#finance`, `#tyre` | |

Starter campus rooms that now carry a department: Security Operations (`security`), Marketing Studio (`marketing`), HR (`hr`), Procurement (`procurement`, was `operations`), Legal Office (`legal`), Compliance Office and Governance Office (`compliance`), Development, Deployments and Incidents (`technology`), Research Room, Intelligence Desk and R&D (`research`). Rooms with no matching department (Facilities, Command Center, AI Organization, AI Labs, Data, Automation, Future Businesses, Executive Office, lounges, reception) stay unset on purpose. Executive and the tower rooms keep opening the executive desk through the building kind.

### The starter campus fill rule

The starter campus never rewrites rooms. The one exception, so rooms from an earlier run can open their module: a re-run sets `team_department` on an existing room of the master list (matched by its stable id such as `campus-02-commerce-marketing-studio`) only while that department is empty or `custom`, and only to the department the master list names. A department the owner chose is never replaced, and nothing else about the room changes. The plan reports it (`department_set` on the room, `counts.update.room_departments`, `totals.update`), the hash covers it, the dialog lists it ("Existing campus rooms that get their department: N") and the owner confirms with a click as before. The change runs inside the building's transaction under the workspace lock, re-checks the room under a row lock, bumps the room revision and is written to the audit record (`after.departments_set`). A second run finds nothing to do.

## 2. Agent roles for the new departments

Governed provisioning (`docs/ANUM_MINE_AGENT_PROVISIONING.md`) gets four more roles. The review dialog now also shows the agent's instructions and its knowledge access. All roles are created paused, with read automatic, write `ask` or `off`, outward actions `off`, a monthly cost cap and only tools and skills that exist in the registry. A capability the platform lacks is listed as missing and the role shows as Limited; a test fails the day a registered tool matches one of the missing patterns.

| Role (id) | Department and room link | Tools that exist | Skills | Cost cap per month | Missing today (Limited) |
|---|---|---|---|---|---|
| Marketing Specialist (`marketing_specialist`) | `marketing` | `marketing.summary`, `marketing.draft_social_post`, `brevo.list_campaigns`, `brevo.campaign_report`, `brevo.draft_campaign`, `brevo.list_lists`, `web.fetch_page` | `anum.brevo`, `anum.document-drafting` | USD 6 | advertising account reports; website and search analytics. It cannot publish, schedule or send: those tools are high risk and are never attached, so the skill `anum.marketing` (which needs scheduling) is not attached either |
| Technology Coordinator (`technology_coordinator`) | `technology` | `web.fetch_page`, `search_email`, `read_email`, `classify_mail`, `summarize_mail`, `team.assign_task` | `anum.email`, `anum.team-assign`, `anum.document-drafting` | USD 6 | reading repositories and pull requests; build and deployment status; a monitoring and incident feed. It does not change code: it writes briefs the owner can paste into Developer mode |
| Research Analyst (`research_analyst`) | `research` | `web.fetch_page`, `search_email`, `read_email`, `summarize_mail`, plus workspace knowledge base access | `anum.email`, `anum.document-drafting` | USD 8 | web search (needs a search service connected, so the skill `anum.web-search` is not attached); reading files from the Documents library |
| Legal Reviewer (`legal_reviewer`) | `legal` | `search_email`, `read_email`, `summarize_mail`, `classify_mail`, `draft_reply`, `team.assign_task`, plus workspace knowledge base access | `anum.email`, `anum.document-drafting`, `anum.team-assign` | USD 5 | reading contracts from the Documents library; a contract register; sending for signature. It gives no legal advice and marks risks as questions for a qualified person |
| Compliance Reviewer (`compliance_reviewer`, existing) | now linked to `compliance` rooms | unchanged | unchanged | USD 4 | unchanged |

Every role's instructions now follow one structure: who you are, what you do, what you hand back, what you never do. The cost caps of the older roles were tuned (finance 4, fleet 6, dispatcher 3, compliance 4; orders, sales and support stay 5).

Staff desks: the staff desk mapping (which desk a role sits at) is built in another change that is not on `main` yet. Until it lands, a new role is seated by the existing placement rule: a vacant desk in a room linked to its department, else a new desk if the room has capacity, else it is created unseated and the dialog says so. When the staff desk code lands, it needs to map `marketing_specialist`, `technology_coordinator`, `research_analyst` and `legal_reviewer` by `room_department`; nothing else in these templates depends on it.

## 3. Meetings tied to rooms

`agent_meeting_sessions.room_id` (nullable, migration `0108_departments_meeting_room`) links a live meeting to the office room it is held in.

- Database: composite foreign key `(tenant_id, workspace_id, room_id)` to `office_rooms`, so a room of another workspace or tenant cannot be linked even by a direct write; `ON DELETE SET NULL (room_id)` so deleting a room clears only the link and keeps the meeting; a partial index for lookups. Existing meetings keep `room_id` null. Forced row level security and the purge policies of the table cover the column.
- API: `POST /api/v1/agent-meetings` accepts `room_id`. The room is read through the caller's own workspace session: another workspace's room is `404 Room not found in this workspace`, a room that is not of kind `meeting` is `422 Only a meeting room can hold a meeting`. The id is returned by create, get, list and the room bundle. `GET /agent-meetings?status=active&room_id=` filters. The audit records `agent_meeting.create` and `agent_meeting.state_change` carry `room_id`, and the stored `state` event payload carries it too. Transcript access rules are not touched and no transcript is shown anywhere in this change.
- A meeting without a room stays valid (the field is optional) and counts as a live meeting in the totals but belongs to no room.
- Web: the office reads active meetings (`?status=active`) and their participants. A room with an active meeting shows **In a meeting** in the 3D label card (and the scene mark), in the list, and in the room inspector, with the meeting title and only the agents stored as participants (names come from the presence records the viewer may see, otherwise "Private agent"; people who joined are a count, never a name). The room's module link opens that very meeting (`#office/meeting/<id>`); a room whose department opens another module also shows "Open the live meeting". A finished, cancelled or failed meeting stops showing as soon as the office refreshes. The scene gets `liveMeetingRoomIds` from this evidence inside `HeadquartersLive` (a host may still pass extra ids); the shell does not need to.
- Starting a meeting: in a meeting room with no live meeting the owner sees **Start a meeting here**, which opens the meeting sessions panel with that room chosen. The panel's create form has a room list (this workspace's meeting rooms, or no room). Anyone else sees who can start one.
- Not built: a scheduled or paused meeting does not mark the room (only `active` does); one live meeting is shown per room; moving a meeting to another room after creation is not offered; the executive building's own room panel (`renderRoomPanel`) is not replaced.

## 4. Tests

- API unit: `tests/test_office_provisioning.py` (catalogue honesty, least privilege, cost caps, new departments, every non-missing tool and skill resolves against the live registry), `tests/test_office_layout.py` (enum, constraint literal and API literal in step), `tests/test_office_starter_campus.py` (departments of the master list, the fill rule).
- PostgreSQL as the app role: `tests/test_postgres_departments_meeting_rooms.py` (new departments storable and unknown refused, room link stored and returned and audited, kind and workspace validation, direct cross-workspace write refused by the foreign key, room deletion clears only the link, starter campus fill with audit and idempotence, provisioning proposal and confirm for a new department) and the existing office suites.
- Web unit: `test/hqRoomModule.test.ts` (department table, labels in both languages, meeting link), `test/hqLabelsPresence.test.ts` (room to meeting derivation).
- Browser: `e2e/hq-departments-meeting-rooms.spec.ts` (each new department opens its module from the inspector and the list, no module is honest, builder choices, in meeting appears and disappears, no WebGL, Arabic, start from a room, review dialog, five viewports).
