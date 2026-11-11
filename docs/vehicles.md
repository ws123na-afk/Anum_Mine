# Vehicles, fuel and mileage

Module 1 of the five-module expansion (owner spec, 4 November 2026). A person keeps their own cars and the workspace keeps its business vehicles: the vehicle master, odometer readings, fuel entries, trips, service records, fees and fines, expiry and service reminders, and what each vehicle costs. The iPhone starts and ends driving sessions with a Shortcut.

**The Finance ledger is the single financial truth.** Nothing in Vehicles posts money. A fuel entry, a service record or a vehicle's purchase holds quantities, odometer values and details, and points at a canonical Finance transaction through [`finance_links`](finance-links.md) (dimension `vehicle`). A SAR 150 fuel payment reported by Apple Pay and by the bank SMS is one canonical transaction; linking it to a vehicle, and allocating it to a business as well, adds no entry, posting or transaction.

Code: `services/api/anum_api/vehicles/` (`models.py`, `store.py`, `service.py`, `analytics.py`, `api.py`, `devices.py`, `jobs.py`), `db/vehicles_repository.py`, migration `0135_vehicles`; web `apps/web/src/VehiclesView.tsx`, `VehicleParts.tsx`, `lib/vehicles.ts`, `lib/vehiclesApi.ts`, `lib/messages/vehicles.{en,ar}.ts`; contracts `packages/contracts/src/vehicles.ts`. Help: [English](help/vehicles.md), [Arabic](help/vehicles.ar.md).

## Who can do what

| | Owner | Member | Viewer |
|---|---|---|---|
| See business vehicles and their records (`vehicle:read`) | yes | yes | no |
| Add vehicles, log fuel, trips, service and readings (`vehicle:write`) | yes | yes | no |
| Change or delete a business vehicle someone else added, or their records (`vehicle:manage`) | yes | no | no |
| Link a payment (also `finance:manage`) | own payments | own payments | no |

- A **personal** vehicle and every record of it are visible only to its owner, whatever their role (row policy). Another member gets 404 for it, the same as for a vehicle that does not exist.
- A **business** vehicle is visible to every member who may use vehicles. Each of them logs fuel, trips and service on it. The person who added it, or a workspace owner, changes, archives, corrects the odometer of or deletes it; a record is changed by the person who logged it or a workspace owner.
- A vehicle's ownership and owner never change (a database trigger refuses it), because the row policies rely on them.
- **Personal banking stays private.** Only the person who logged a record can link a payment to it, and the payment picker lists only their own Finance ledger. Other members see the linked amount and currency on a business vehicle's record, never the payer's ledger id, account, card, merchant text or message.

## Records

| Record | What it holds |
|---|---|
| Vehicle | Registration (unique per country among active vehicles of the workspace), VIN (11 to 17 characters, no I, O or Q; lists show only the last four), nickname, make, model, year, type, country SA or AE, currency (SAR or AED by default), fuel type, tank size, acquisition date, purchase price, residual value, useful life, insurance, registration and inspection expiry, notes, archived. |
| Odometer reading | Kilometres, time, source (`manual`, `fuel`, `trip_start`, `trip_end`, `service`, `device`, `correction`), the record it came from, and for a correction its reason. |
| Fuel entry | Time, station, fuel type, litres, price per litre, pump total, currency, odometer, full or partial fill, payment method, receipt (vault document id), notes, and the read copy of its payment link. |
| Trip | Start and end time, start and end odometer, distance and how it was worked out (`typed`, or `previous_reading` when only the end was typed), purpose (business, personal, commute), what for, project, from and to (only when the person turned on **Store trip places**; places stored earlier stay and may be resent unchanged after the switch is turned off), notes, source (web or iPhone). At most one trip in progress per person and vehicle. |
| Service record | Kind (maintenance, oil, tyres, battery, repair, inspection, insurance, registration, fine, toll, parking, warranty, wash, other), date, odometer, workshop or issuer, description, reference or fine number, next due date or kilometres, warranty until, trip (for tolls and parking), receipt total, currency, payment method, and the read copy of its payment link. |

The pump total and the receipt total are **details**: they help find the payment and check it (a linked payment more than 5 % away from the pump total is flagged), but they are never counted as spend.

## The odometer never goes down

Readings are ordered by time. A reading that is not a correction must be at least the reading before it and at most the reading after it (unless that later reading is itself a correction, which starts a new run). The API checks this under a lock on the vehicle row and answers `409 odometer_decrease` with the reading it would break; the database trigger `vehicle_odometer_monotonic` refuses the same rows, so a concurrent request cannot slip past. A **correction** (cluster replaced, a typing mistake) needs a written reason (`ck_vehicle_odometer_readings_reason`) and the right to change the vehicle. Readings are never edited; a reading leaves only with the record it came from.

The phone never reads the car's odometer. Every value is one a person typed.

## Payments

Each fuel entry, service record and the vehicle's purchase has a **Payment** action:

- **Pick a payment.** `GET /api/v1/vehicles/payments` lists the caller's own live payments (never income or transfers) within 60 days before and 15 days after the record, ranked by closeness to the pump or receipt total and the date, with what is left to link to a vehicle. Choosing one calls `finance.links.create` with dimension `vehicle`, target `vehicle_fuel_entry`, `vehicle_service_record` or `vehicle`, and the whole remaining amount (or a given part; a payment in more than one currency needs `currency`). A record has **one** live vehicle link: the link's idempotency key names the record, and `finance.links.create` holds the owner's lock, so two tabs linking different payments at once cannot both succeed. The `vehicle` dimension is registered with `single_payment`, so the generic `POST /api/v1/finance/links` refuses a second live payment on the same vehicle record too (`409 target_linked`). Linking the same payment again answers `200 replayed`; a record linked to another payment answers `409 already_linked`; over the payment answers `409 over_allocated`.
- **Paid in cash.** The cash payment is recorded **once** in Finance through the same path as a hand-entered transaction (`finance.api.record_manual_transaction`, the journal's `manual` source), then linked. The transaction id is derived from the record and the request's `Idempotency-Key`, so a retry finds the same transaction instead of adding another; an earlier cash payment of the record with other figures (unlinked, then entered again) is never reused. If another tab linked a payment first, the cash transaction this request recorded is reversed in the same way as deleting it in Finance, so it never stays a posted expense.
- **Proposed from a scanned receipt.** When a fuel entry or service record names its receipt (a vault document whose scanned values were confirmed, [Receipt matches](documents.md#receipt-matches)), Finance, Suggestions proposes the payment for it (`attribution`, `params.source: receipt`): the record's typed total, else the receipt's total and date, in the same currency within 3 days (7 for a service); a payment whose words name the receipt's merchant is proposed even without fuel or service words, and one whose words name another merchant never is. A typed pump total that disagrees with the receipt is proposed on the typed figure, without the receipt. **Accept** links it with every rule above; nothing is linked by itself. Tests: `services/api/tests/test_receipt_matches.py`.
- **Remove link.** Voids the link (repeating it changes nothing). The payment stays in Finance; the page says to delete it there if it was a mistake.

The record keeps a **read copy** of its link (`payment`: link id, canonical id, status, amount, currency, who paid, recorded as cash). `finance.links` calls the vehicle module's observer (`register_target(..., observer)`) inside the link's own transaction whenever a link is made or changes, so the copy commits or rolls back with it:

| In Finance | The vehicle record |
|---|---|
| Linked | `active`: the amount counts as the vehicle's cost |
| Corrected, same figures | unchanged, same canonical id |
| Corrected below the linked amount, or another currency | `needs_review`: not counted; the owner confirms or removes the link under Finance |
| Undone | `void` with `transaction_undone`: not counted; the record says the payment was undone |
| Statement reconciled into a recorded payment | follows the link to the recorded payment's canonical id |
| Link removed by the owner | cleared |

Deleting a record removes its link first (the payment stays). A vehicle with any linked record cannot be deleted (`409 has_payments`): archive it to keep its history.

## Business allocations

A business vehicle is often paid from the owner's personal card: the payment sits in the owner's own Finance ledger (their personal workspace) and the owner allocates it to the business under Finance ([Business allocations](finance-links.md#business-allocations)). The business workspace sees only the allocation row (date, amount, currency, category, memo, the six character source reference), never the card, account or merchant text. That allocation is now shown as the business vehicle's cost (`vehicles/service.py`):

- **Use a business allocation.** On a fuel entry or service record of a **business** vehicle, the person who logged it opens **Payment**, **Business allocation**, and picks one of this business's allocations they made that no vehicle record counts yet (`GET /api/v1/vehicles/allocations?around&amount`, nearest to the record's figure and date first). `POST /api/v1/vehicles/costs/{fuel_entry|service_record}/{id}/allocation {allocation_id}` stores it as the record's cost (`payment.source: allocation`, the allocation id and its source reference; `link_id` and `canonical_id` stay empty, so no ledger id ever reaches the business). Needs `vehicle:write` and `business:read`; a personal vehicle is refused (`422 not_business_vehicle`), a record someone else logged (`403 not_record_owner`), an allocation that is not this business's or not the caller's (`404 allocation_not_found`), one another record already counts (`409 allocation_used`, checked under a workspace advisory lock) and a record that already has a cost (`409 already_linked`). Repeating it answers the same cost (`200`, `replayed`).
- **Counted like a payment.** It is the record's cost in the overview, the vehicle's report, cost per km and total cost of ownership, per currency, like an active payment link; the record shows "SAR 150.00 (business allocation)" and the vehicle's CSV export says `allocation_active` (or `allocation_void`) in `payment_status`.
- **Follows Finance.** The record is read with the allocation row as it is now: if the owner takes the allocation back or the payment is undone (the row is removed), the record reads `void` with `allocation_removed` ("The allocation was taken back in Finance") and is not counted. The stored copy is not rewritten on a read; picking another allocation or **Remove allocation** (`DELETE .../allocation`, idempotent) replaces it.
- **Both are never one record.** A record has one cost: a linked payment or an allocation (`409 already_linked` either way). The vehicle's purchase is linked as a payment only.

Audit: `vehicle.allocation.use`, `vehicle.allocation.release` (ids only). No migration: the cost lives in the record's existing `payment` JSON (expand only; the previous release reads it as a payment of that amount). Tests: `services/api/tests/test_vehicle_allocations.py`, `tests/test_postgres_finance_links.py::test_a_business_vehicle_counts_the_owners_allocation_as_its_cost`, web `test/vehicles.test.ts`, `e2e/vehicles.spec.ts`, phone `apps/mobile/test/vehicles/vehicles_test.dart`.

## Analytics

Pure functions in `vehicles/analytics.py`, tested on their own:

- **Spend** from active linked payments only, per month, per category and per vehicle, **per currency**. Nothing adds SAR to AED.
- **Distance** is the sum of increases between consecutive readings; the jump to a correction is not distance.
- **Usage per month** (`usage`, quantities only): kilometres and litres per month. A month's kilometres are the increases whose later reading falls in that month, so the run from the last reading of the month before counts too. The overview also gives each vehicle's kilometres, litres and average L/100 km in the period (`by_vehicle_usage`).
- **Cost per km** per currency: spend in the period divided by the distance in the period.
- **L/100 km only from full-to-full chains:** from one full fill with an odometer value to the next, counting the litres of every fill after the first (partial fills included). A full fill without an odometer value, or an odometer correction in between, breaks the chain.
- **Fuel price trend:** litres-weighted price per litre per month, currency and fuel type.
- **Anomalies:** more litres than the tank (5 % slack), consumption over 1.5 times the vehicle's median (three segments or more), a price per litre more than 25 % from the median (three prices or more), a linked payment more than 5 % from the pump total, a payment changed or undone in Finance.
- **Total cost of ownership:** running costs, all time, per currency; and next to them, never inside them, **straight-line depreciation** of the purchase price to the residual value over the useful life (monthly amount, months so far, accumulated, book value). A capital purchase is an asset, not an operating expense.

## Reminders

The daily retention job (`python -m anum_api.voice_retention`, reported under `vehicles`) runs `vehicles.jobs`: for each vehicle owner with **Reminders** on, one in-app notification (kind `vehicle_reminder`, never a push or e-mail, since a registration is personal data) when insurance, registration or inspection expires or a service falls due by date, at the person's reminder days (30 by default), at 7 days, at 1 day and on the day; and once when a service is within 500 km by the odometer. Each step is sent once per due date (`vehicle_reminders`, unique); a new date starts again; archiving the vehicle or turning reminders off stops them. A reminder whose notification fails is not marked, so the next run tries again. Owners are discovered as `anum_maintenance` (scope and date columns only, never BYPASSRLS); the work runs as the application role in each owner's own scope.

## iPhone driving sessions

Integration `vehicle.driving`, scope `vehicle`, permission `vehicle:write`, off until the owner turns it on under Settings, Integrations, iPhone ([iPhone integrations](iphone-integrations.md)), 10 requests a minute and 300 a day per device. `POST /api/v1/vehicles/device/sessions` with the shared device token (`Authorization: Bearer <token>`, HTTPS only; without a device token the ordinary sign-in applies):

| Field | |
|---|---|
| `action` | `start`, `end` or `test` |
| `vehicle` | The registration (spaces ignored) or id; empty means the default vehicle (Vehicles, Settings), or the only vehicle |
| `occurred_at`, `idempotency_key` | When it happened; a retry with the same key is saved once |
| `odometer_km` | End only: what the person typed. The phone never reads the odometer |
| `purpose` | Optional: business, personal or commute |
| `fuel_litres`, `fuel_total`, `fuel_full`, `parking_total` | Optional, end only: a fuel entry and a parking record waiting for their payments |

Start opens a trip (a second start answers `already_started`); end closes the trip in progress (with no trip in progress, an end within 10 minutes of a device trip this person ended is treated as a retry and adds nothing) with the typed odometer (distance from the start reading, else from the last reading before the trip started; none without a typed value), or saves the reading alone when no trip was in progress. Every answer has a plain `message` for the Shortcut to show. Refusals after the token was accepted (`vehicle_not_found`, `vehicle_required`, `odometer_decrease`, `trip_open`) are recorded as failed events on the iPhone page. The exact Shortcut steps are on that page (`IPHONE_GUIDES`, `vehicle.driving`): a CarPlay or Bluetooth **When Connects** automation sends `start`; a **When Disconnects** automation asks for the odometer and sends `end`.

iOS may delay automations while the phone is locked or in Low Power Mode, and CarPlay and Bluetooth triggers need real-device testing (status: Device).

## API

| Route | |
|---|---|
| `GET /api/v1/vehicles` (`q`, `archived`, paging) | `vehicle:read`; VINs masked, odometer and what is due |
| `POST /api/v1/vehicles` | `vehicle:write`; optional first `odometer_km` |
| `GET`, `PATCH /api/v1/vehicles/{id}` (`version`), `POST /{id}/archive`, `DELETE /{id}` | read; change needs the vehicle's owner or `vehicle:manage` |
| `GET /api/v1/vehicles/overview` (`start`, `end`), `/reminders`, `GET`, `PUT /preferences` | spend per currency, kilometres and litres per month and per vehicle, due items, waiting records; the person's settings |
| `GET /api/v1/vehicles/{id}/report` (`start`, `end`) | the analytics above |
| `GET`, `POST /api/v1/vehicles/{id}/odometer` | readings, newest first; a correction needs `reason` |
| `GET`, `POST /api/v1/vehicles/{id}/{fuel,trips,service}` (`q`, `start`, `end`, paging; `Idempotency-Key`) | records; a replay answers 200 with `replayed: true` |
| `PATCH`, `DELETE /api/v1/vehicles/{fuel,trips,service}/{record_id}` | the record's author or `vehicle:manage` |
| `GET /api/v1/vehicles/payments`, `POST /costs/{fuel_entry,service_record,purchase}/{id}/payment`, `/cash`, `DELETE .../payment` | plus `finance:manage`; the caller's own payments only |
| `GET /api/v1/vehicles/allocations`, `POST`, `DELETE /costs/{fuel_entry,service_record}/{id}/allocation` | plus `business:read`; a business vehicle's cost from this business's allocations the caller made ([Business allocations](#business-allocations)) |
| `GET /api/v1/vehicles/{id}/export.csv` | the vehicle's log; cells that start with `=`, `+`, `-` or `@` are escaped |
| `POST /api/v1/vehicles/device/sessions` | the iPhone (above) |

Refusals answer `{"detail": "<plain words>", "code": "<code>"}` with figures where they help. Audit rows hold ids and codes only, never a registration, VIN, place or amount.

## Web page

`#vehicles` (`src/VehiclesView.tsx`, `src/VehicleParts.tsx`, figures in `src/lib/ops.ts`): the page head with Add vehicle, a month picker and the tabs **Overview**, **Vehicles** (`#vehicles-list`) and **Settings** (`#vehicles-settings`).

- **Overview**: active vehicles, kilometres this month and spend this month (each against the month before), and services or renewals due soon with the records waiting for a payment. The fleet table (vehicle, registration, odometer, this month's spend, the next service or renewal, status: overdue, due in N days or in service) with search on the server and status, ownership and country filters; up to 100 vehicles, with a link to the full list beyond. The selected vehicle's panel: latest odometer, next due, fuel economy in km per litre, this month's cost, litres and kilometres over six months (`/report` `usage`), the maintenance timeline (last service and what is due), its papers (registration, insurance, inspection with their dates), its newest fuel, service and trip records, Open vehicle, Export CSV, and Record fuel, Log trip and Add service (the same forms as the vehicle page) and Upload document (the document vault). Below the table: what is coming up and spend by month.
- A vehicle opens its own page: Fuel, Trips, Service, Odometer, Report and Details. `#vehicles?vehicle=<id>` opens that page by link (the search palette uses it); leaving the page drops the link from the address. A vehicle that is gone or not visible shows the page's load error with the way back.

Not built: fleet-wide Fuel and mileage, Service and Documents tabs (each vehicle's page has them) and a fleet-wide export (each vehicle exports its own log).

## Mobile

Menu, **Vehicles** (`/vehicles`, `apps/mobile/lib/features/vehicles/`, [Record modules](mobile.md#record-modules)): the list with search, active or archived, paging and the reminders due (`GET /reminders`); a vehicle with its details, due items, and its fuel, trips and service records, each paged; adding a vehicle, a fuel entry, a trip with its start and end odometer and times, a service record (with the next due date or kilometres), and an odometer reading. Each add sends one `Idempotency-Key` per form, so a retry after a lost answer is stored once. The `odometer_decrease` refusal is shown with the server's sentence (the reading and date it conflicts with) and an **Add a correction** button that opens the reading form with the typed kilometres and the correction switch on; the correction needs a reason. **Link a payment** lists the person's own Finance payments (`GET /payments`) and links one to a fuel entry or service record; linking adds no money. People who may not change a vehicle see why. From 840 px the list and the selected vehicle sit side by side. Trip places (from, to) and the CSV export stay on the web. Tests: `apps/mobile/test/vehicles/vehicles_test.dart`. On a business vehicle a fuel or service record also offers **Use a business allocation** (the same picker, this business's allocations the caller made) and shows such a cost as a business allocation, or that it was taken back in Finance.

## Storage, privacy and deletion

Migration `0135_vehicles` (expand only, after `0134_finance_links`): `vehicles`, `vehicle_odometer_readings`, `vehicle_fuel_entries`, `vehicle_trips`, `vehicle_service_records`, `vehicle_reminders`, `vehicle_preferences`. Every table has forced RLS with the tenant policy, the restrictive `vehicle_visible_<table>` (business rows to the workspace, personal rows to their owner; the child rows carry the vehicle's visibility and owner, copied and checked by the trigger `vehicle_child_matches`, so no policy looks another table up), the restrictive `vehicle_insert_<table>` (a row is written by the person it names) and `lifecycle_purge_<table>`. All are listed in `tenant_lifecycle_tables.py`.

The privacy export includes the person's own vehicles, their records on business vehicles, their reminders and settings. **Delete my data** removes their personal vehicles with every record, their own records on business vehicles, their reminders and settings; business vehicles they added stay with the business.

## Tests

`tests/test_vehicles_api.py` (rules, permissions, idempotency, the acceptance cases: Apple Pay plus SMS stays one expense after linking and allocating, relinking and unlinking are idempotent, a correction keeps the link, an undo voids it, cash is recorded once, the iPhone sessions with a scoped token), `tests/test_vehicles_analytics.py` (the pure functions), `tests/test_postgres_vehicles.py` (forced RLS and visibility, the odometer trigger and concurrent readings, the read copy following correction and undo in PostgreSQL, cash under RLS, reminder discovery and privacy delete), web `test/vehicles.test.ts` and `e2e/vehicles.spec.ts`.

## Not built here

- On the phone: editing or archiving a vehicle, trip places, recording a cost as cash, preferences and the analytics (on the web).
- Tyre Pulse fleet vehicles stay a read-only snapshot in the Tyre module; there is no link from a Tyre Pulse vehicle to an ANUM vehicle yet.
