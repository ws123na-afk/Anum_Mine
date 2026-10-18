# Tyre Pulse: sales kit

Slug `tyre-pulse` (the alias `TyrePulse` also matches). Catalog entry: [`scripts/business/products.json`](../../../scripts/business/products.json), loaded by `scripts/business/seed-products.py` ([Product sales kits](index.md#loading-the-catalog)).

Sources (read only, in the separate Tyre Pulse repository): `FEATURES.md`, `CODEX_CONTEXT.md` and the marketing site under `marketing/` (home, industries, pricing and `lib/site.ts`); in this repository the [business roadmap](../../ANUM_OS_Business_and_Product_Master_Roadmap.md) sections 2.1 and 2.2. That repository has no `README.md`. Anything not in those sources is `-`.

## What it does (verified features)

Tyre Pulse is a commercial multi-tenant platform for plant, machinery and vehicle (PMV) operations: fleet, tyres, inspections, workshop, accidents and reporting. Two apps share one database: a web console for the office and managers, and a mobile app for inspectors and tyre staff. Website: `www.tyrepulse.app`.

**Web console**
- Dashboard with cost, risk, recent activity and open issues, filtered by date and country.
- Tyre records: every tyre issue, change and cost, with search, filters and bulk edit.
- Inspections: schedule, complete and attach findings; overdue ones turn red.
- Accidents and claims: responsibility and liability, parts and repair costs, insurance claim and money recovered (net cost after recovery), case log, closure approval, and a who-changed-what activity tab.
- Smart importer for Excel, OpenDocument and CSV files: finds the header row, maps columns (Arabic headers too), shows a data quality report and a cleaning preview before upload, never drops rows silently.
- Data cleaning review with an optional AI helper; custom data for columns that match no standard field.
- Reports and an executive report, exported to Excel or PDF or emailed.
- Analytics: fleet, brand performance, site, country and period comparison, KPI scorecard, engineering KPI, predictive maintenance, cost centre, fuel efficiency, vendor and position intelligence.
- Procurement, budgets, stock and work orders; preventive maintenance by hours, kilometres or date; job cards with labour, parts and outside services.
- QR and barcode labels; asset registry with a health view; real-time alerts; an AI assistant over the fleet data; audit trail; roles and module permissions.

**Mobile app (inspector)**
- English, Arabic and Urdu with right-to-left layout; sign in with email, username or employee ID.
- Tyre inspection on a top-down vehicle diagram (condition, pressure, serial, photo, notes), scan a QR or barcode to start, serial lookup.
- Accident reporting with photos, claims and recovery, closure approval for managers, accident PDF export.
- Works offline and syncs when the signal returns.

**Security:** row-level security on every table by role, two-factor sign-in, server-side keys only, HSTS and `security.txt` on the web app.

## Ideal customer profile

| Field | Value |
|---|---|
| Industry | Construction, transport and logistics, ready-mix concrete, equipment rental, workshop networks, government and enterprise fleets (the marketing site's industries page) |
| Size | Fleets of about 50 or more vehicles and machines over one or more sites (recommendation, owner to confirm) |
| Region | GCC first: UAE and Saudi Arabia (multi-country reporting by country and currency is built in), then the rest of the GCC |
| Buyer roles | Fleet manager, workshop manager, operations or plant manager; finance controller for cost per kilometre (roadmap 2.1: fleet, workshop or operations manager) |
| Trigger events | Rising tyre spend or tyre failures; accidents and insurance claims tracked on paper or spreadsheets; a new project or site mobilising a fleet; a client or auditor asking for inspection proof |
| Disqualifiers | Fewer than about 10 vehicles; locked into an enterprise fleet suite on a long contract; nobody able to share tyre or fleet data for a pilot |

## Value proposition

Capture tyre, inspection and accident data once, in the field and offline, and get cost, risk and the next action per asset, site and country.

| Pain | Outcome |
|---|---|
| Tyre and maintenance data scattered over spreadsheets and paper, with weak cost visibility (roadmap 2.1) | One record per tyre and asset; the importer takes the existing spreadsheets with a data quality report, and cost rolls up by asset, site and country |
| Worn or under-inflated tyres found only after a failure | Inspectors record condition and pressure on a vehicle diagram, even offline; risky tyres raise alerts and inspections that are overdue turn red |
| Accident claims and insurance recoveries lost track of | Each accident has its parts, claim, recovery, case log and closure approval, with the net cost after recovery and a full change history |

## Packaging and pricing

| Package | Contents | Price |
|---|---|---|
| Tyre Pulse 30-day pilot | Limited fleet dataset imported from the customer's spreadsheets, data quality report, tyre, replacement and cost report, inspector app for the pilot site, one review meeting | `-` |
| Solo | For an owner managing a small fleet: core asset and tyre records, inspections, basic dashboards, standard exports | `-` |
| Team | Multiple users and sites, approvals, maintenance and inventory, PDF and Excel reports | `-` |
| Professional | Advanced analytics, scheduled reports, TV dashboards, API and automation options | `-` |
| Enterprise | Custom users and assets, SSO and security controls, data migration and integrations, SLA and priority support | `-` |

Plan contents are from the marketing site's pricing page, which quotes every plan on request "by fleet size, users, modules, countries and integrations" and never publishes a figure.

**Recommendation (owner to confirm):** the roadmap's test price hypothesis for the pilot is SAR 750 to 1,500 (section 2.2; a hypothesis, not market research). Promise only functions tested in the pilot and no savings guarantee. The seed loads the pilot as a draft `quote` offer; the owner sets the price and activates it.

## Short pitch

Tyre Pulse gives fleet and workshop teams one place for tyres, inspections, maintenance and accidents. Inspectors work on the phone, offline, in Arabic, English or Urdu; your existing spreadsheets import with a data quality check; managers see cost and risk per asset, site and country and approve from the phone. Start with a 30-day pilot on part of your fleet.

## Email opener template

> Subject: Tyre cost per asset at {{company}}
>
> Hello {{first_name}}, I noticed {{company}} {{verified_observation, for example "runs mixers across three sites in Dubai"}}. Fleet teams we talk to usually keep tyre, inspection and accident records in spreadsheets, so tyre cost and failures only show up at month end. Tyre Pulse imports those spreadsheets as they are, lets inspectors record tyres on the phone even without signal, and shows cost and risk per asset and site. Would you be open to a 30-day pilot on one site's data, ending with a tyre and cost report you keep either way? If this is not relevant, reply "no" and I will not follow up.

Fill `{{verified_observation}}` only from a recorded evidence source on the lead ([Business: evidence](../../business.md#evidence-and-inferences)). A person approves and sends the draft ([Business: drafts](../../business.md#drafts-sending-is-off)).

## Objection handling

| Objection | Answer |
|---|---|
| "Our data is messy." | The importer finds the header row, maps columns including Arabic headers, and shows a data quality report and cleaning preview before anything is saved. Messy files are the normal case. |
| "Our sites have no signal." | Inspections save on the phone and sync when the connection returns. |
| "We already have a fleet system." | Tyre Pulse goes deeper on tyres, inspections and accident claims; the pilot runs beside the current system on exported data, so nothing has to be replaced to judge it. |
| "How much will we save?" | No savings figure is promised. The pilot report shows your actual tyre cost and failure picture; you decide from that. |
| "Who sees our data?" | Every table has row-level security by role; sign-in supports two-factor; keys stay on the server. |
| "What does it cost?" | Plans are quoted by fleet size, users, modules, countries and integrations; the pilot has a fixed scope and price. |

## Proof points (verified only)

- In production use by at least one fleet operation: production deployments, live database migrations and authenticated checks with real workshop checklists and inspections are recorded in `CODEX_CONTEXT.md` (September 2026). Customer name: `-` (owner to confirm whether it may be named).
- CI evidence recorded in `CODEX_CONTEXT.md`: on 24 September 2026 the general CI passed all five jobs including 702 web test files, and Flutter CI passed 2,740 tests with Android and iOS builds.
- A platform audit covered 256 web pages, 28 Flutter features and 7 marketing pages (`audit/platform-review-2026-09-24/review.md` in that repository).
- Testimonials, savings figures, fleet counts that may be published: `-`.

## Owner questions

1. Confirm the pilot price (the roadmap suggests testing SAR 750 to 1,500) and the plan prices, or confirm "quote on request" only.
2. May the current production customer be named, or described (industry, fleet size), in outreach?
3. Confirm the ideal fleet size and the minimum fleet to qualify.
4. Is there a published app store listing to link (the site has store badges)?
