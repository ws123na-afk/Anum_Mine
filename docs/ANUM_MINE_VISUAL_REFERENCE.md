# ANUM MINE: visual reference and build plan

**Date:** 22 October 2026 | **Status:** owner-approved visual direction, partly built. Supersedes the "visual reference not yet provided" note in section 8 of the [Virtual Headquarters master plan](ANUM_MINE_VIRTUAL_HEADQUARTERS_MASTER_PLAN.md).

The owner supplied five concept renders (22 October 2026). They are **concept art with invented names, numbers and weather**. They fix the look, layout and interaction. They are not data: every figure, name and status in the real screens comes from a verified backend record, and anything without a source shows an honest "not connected" or "unavailable" state (CLAUDE.md: no mock data, no dead buttons).

## Look and feel (all screens)

- Isometric night-time architectural renders: warm interior light behind glass, dark navy sky, palm and garden landscaping, water features, vehicles. A day setting exists for every scene (Day / Night toggle, default follows the person's saved choice, then the device).
- Brand: "ANUM MINE" wordmark with the triangular A logo, tagline **"Your AI Team. Real Work. Real Results."** (campus tagline in the top bar: "Your AI Organization. One Center. Everything.").
- Floating **label cards** pinned to buildings, rooms and desks: dark translucent glass, small icon tile, title, one subtitle line, a status dot. Agent cards are white with avatar, name, role and a coloured status pill (Working green, In Meeting amber, Observing violet, Idle blue/grey, Offline red).
- Palette follows the existing design tokens; accents: emerald (working), amber (meeting), blue (idle), red (offline or blocked).
- Avatars are generic professional characters, selectable, never likenesses of real people.
- Everything must pass the Responsive UI quality gate in CLAUDE.md (five viewport sizes), keyboard use, reduced motion, and a no-WebGL fallback.

## Screen 1: Campus (all businesses)

Isometric campus of separate buildings around a central globe fountain monument labelled ANUM MINE ("People, Agents, Real Work"). A walkway, parking, trees, water, a truck yard for operations.

Buildings in the concept: **Executive HQ** (tall central tower), **Personal Office**, **Tyre Pulse HQ**, **ANUM360 Commerce**, **Operations & Warehouse**, **Finance & Wealth**, **Sales & Marketing**, **Technology & Engineering**, **Research & Intelligence**, **Product Support**, **Support & Customer Experience**, **Legal & Compliance**, **Knowledge Center**. A simpler variant shows five: Personal Office, ANUM HQ, ANUM360 Commerce, Tyre Pulse HQ, Operations & Logistics Hub.

Each building label shows: icon, name, "N agents · M working" or "· M in meeting", status dot, and a short avatar stack.

Chrome around the campus:
- **Left sidebar:** Campus, My Offices, Agents, Tasks (count), Meetings (count), Approvals (count), Projects, Businesses, Personal, Finance, Reports, Knowledge, Inbox (count), Settings. Each links to the existing ANUM module; counts come from the existing APIs.
- **Top bar:** organisation selector, search ("agents, tasks, meetings, files", Ctrl or Cmd K), local date and time, notifications bell, theme toggle, full screen, profile menu.
- **View switch (right):** 3D View, Top View, List View. List View is the accessible list of every building and room.
- **Camera tools (right):** compass (north), zoom in, zoom out, reset, sun (day), moon (night).
- **Bottom centre toolbar:** Rotate Left, previous, next, Rotate Right, Zoom menu, Fullscreen.
- **Bottom left:** "Total Agents" panel with a donut chart and legend Working / In meetings / Idle / Offline with counts.
- **Bottom right:** Campus Map minimap (expandable) showing building footprints and the current view.
- Simpler variant: a "Team Status" strip (Working, In Meetings, Idle, Offline, total) and a Campus / Map segmented control.

## Screen 2: Building cutaway, commerce example (ANUM360 Commerce, Floor 1)

Roofless cutaway of one floor with labelled zones and people at desks.

- Header: "Back to Campus", building icon and name, status dot, subtitle ("Commerce & Care"), live counters (People, Orders, Dispatch Today).
- Floor control: previous floor, "Floor 1", next floor; Rotate, Pan, Zoom, Full Screen.
- Zones with a label card (icon, name, "N online"): ANUM (Chief of Staff and Central Operations, with a wall of live dashboards), Customer Care, Reception, Commerce Coordinator, Product Support, Order Processing, Meeting Room (state "In Meeting"), Marketing Studio, Sales Channels (marketplace logos: Daraz, Shopify, Amazon, TikTok Shop), Dispatch / Packing (shelving, packing benches, loading truck).
- Agent cards beside their desk: avatar, name, role, status pill.
- Flow arrows on the floor between zones (order path).
- Bottom panels: **Live Operations** (total orders, processing, dispatched, customer care, satisfaction), **Current Flow** (Order Received, Processing, Packing, Dispatched), **Team Status** (Working, In Meeting, Idle, Offline).
- Real data: the Live Operations and Current Flow panels read the connected commerce channels (`docs/commerce.md`). A workspace with no commerce connection shows "Not connected" with the setup link, never numbers.

## Screen 3: Building cutaway, Tyre Pulse HQ (Ground Floor)

Same grammar as screen 2.
- Breadcrumb "Campus > Tyre Pulse HQ" and an info card: "Fleet tyre planning, analysis, inspection, procurement and end-to-end operations to keep your fleet moving."
- "Floor View" dropdown (Ground Floor, upper floors).
- Zones: Meeting Room, Tyre Analysis (large wall displays), Inspection Control, Fleet Planning, Reception, Product Support, Procurement, Operations & Warehouse (racking A1, A2, B2, forklift), Dispatch / Loading (truck).
- Team Status panel ("9 agents total": Working, In Meetings, Idle, Offline).
- Real data comes from the Tyre Pulse module (`docs/tyre.md`).

## Screen 4: Headquarters building, three floors

Whole building cutaway, all floors visible, with a floor switch (Floor 1, 2, 3) and a building switcher bottom left ("ANUM HQ, Headquarters, 3 Floors, 9 Teams").
- Rooms: Meeting Room A (client meetings, team sync), Executive Office (strategy, leadership, growth; the owner at the desk), Command Center (operations, performance, AI agents; wall of dashboards and a globe), Finance & Wealth, People & Hiring, Strategy Desk, Systems & Knowledge, Automation Guardian, Reception (visitors, check-in, guest experience), Breakout Lounge.
- Reception shows a visitor flow checklist (Visitor Arrives, Check-in, Host Notified, Badge Issued, Entry Granted). This is decorative unless a real visitor workflow exists; if not, omit it rather than fake it.
- Toolbar: Rotate Left, Rotate Right, Pan, Zoom out and in, Full Screen.

## Data-truth rules (these decide every label)

| Shown in the concept | Real source | When there is no source |
|---|---|---|
| Buildings on the campus | Authorised workspaces and business units of the signed-in person, as buildings in the office registry | Building omitted; owner sees "Add a building" |
| "N agents", "M working", "M in meeting", donut totals | Presence projection (F03): run, task, approval and meeting records with provenance and a last-verified time | "Presence unavailable", never a guess |
| Agent card name, role, status | Agent profile plus presence | Unassigned roster entry |
| Meeting room "In Meeting" and transcript | Live agent meeting sessions (`docs/meetings.md`, migration 0098) | Room shows "No meeting" |
| Orders, dispatch, satisfaction | Connected commerce channels | "Not connected" |
| Weather (28 C, Riyadh) | None | Not shown (date and time only) |
| Visitor check-in flow | None | Omitted |
| Names such as Ayesha Khan, Bilal Ahmed | None, concept only | Never shown as real |
| Cross-workspace totals (executive view) | Explicit owner grant per workspace, minimum data, audited | Each workspace opened by an explicit switch |

## Build workstreams (owner selected 22 October 2026)

1. **Premium 3D headquarters** and 2. **Multi-business campus**: screens 1 to 4 above, 3D scene quality, camera, day and night, minimap, list view, fallbacks.
3. **Live agents and workstations**: presence with provenance, workstation panels with real task checkpoints and approvals, reconnect.
4. **Autonomous AI meeting rooms**: agenda, decisions, action items, interruptions, worker supervision, transcript replay.
5. **Executive PA**: guided tour, navigation, voice or typed commands through a permissioned tool interface, room hand-off.
6. **Executive 360 command centre**: personal, career, businesses, finance and customer work with owner-approved cross-workspace summaries.
7. **Production completion**: CI, PostgreSQL and browser verification, streaming, performance, accessibility, security, deployment checks.

Not in this round: the Flutter companion (F12) and repository reconciliation.

Each workstream is its own pull request. Migrations are added one at a time: chain on the newest revision in `main` (currently `0100_office_room_geometry`) and renumber at merge.
