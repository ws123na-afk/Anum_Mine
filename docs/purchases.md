# Purchases, deliveries, returns and refunds

Module 4 of the five-module expansion (owner spec, 4 November 2026). A person keeps what they ordered and the workspace keeps its business purchases: the order and its items, where it is (an append-only history along a state machine), shipments and tracking numbers, returns with their pickup, proof, confirmation and dispute, the refunds owed, warranties and reminders. The iPhone shares an order confirmation or a delivery message with a Shortcut.

**The Finance ledger is the single financial truth.** Nothing in Purchases posts money. A purchase holds what the shop said (its total, tax and shipping are details) and points at its canonical Finance transaction through [`finance_links`](finance-links.md) (dimension `purchase`, role `payment`). A refund points at the refund's own canonical transaction (role `refund`), linked to the return of the original purchase. **A refund is never income:** only a transaction that gives money back to a spending category may be linked as a refund, and the ledger posts it against that category. A purchase paid by Apple Pay and reported by the bank SMS is one canonical transaction; linking it to a purchase, and allocating it to a business as well, adds no entry, posting or transaction.

Code: `services/api/anum_api/purchases/` (`models.py`, `store.py`, `service.py`, `parser.py`, `api.py`, `devices.py`, `jobs.py`), `db/purchases_repository.py`, migration `0136_purchases`; web `apps/web/src/PurchasesView.tsx`, `PurchaseParts.tsx`, `lib/purchases.ts`, `lib/purchasesApi.ts`, `lib/messages/purchases.{en,ar}.ts`, `purchases.css`; contracts `packages/contracts/src/purchases.ts`. Help: [English](help/purchases.md), [Arabic](help/purchases.ar.md).

## Who can do what

| | Owner | Member | Viewer |
|---|---|---|---|
| See business purchases and their rows (`purchase:read`) | yes | yes | no |
| Add purchases, change their status, add shipments and returns (`purchase:write`) | yes | yes | no |
| Edit or delete a business purchase someone else added (`purchase:manage`) | yes | no | no |
| Link a payment or a refund (also `finance:manage`) | own purchases | own purchases | no |

- A **personal** purchase and every row of it are visible only to its owner, whatever their role (row policy). Another member gets 404 for it, the same as for a purchase that does not exist.
- A **business** purchase is visible to every member who may use purchases. Each of them follows its delivery, adds shipments and asks for a return. The person who added it, or a workspace owner, edits its details and items, archives or deletes it; a shipment or return is changed by its author, the purchase's owner or a workspace owner.
- A purchase's ownership and owner never change (a database trigger refuses it), because the row policies rely on them.
- **Personal banking stays private.** Only the person who added a purchase links its payment and refunds, and the picker lists only their own Finance ledger. Other members see the linked amounts and currencies, never the payer's ledger or link ids, account, card, merchant text or message.

## Records

| Record | What it holds |
|---|---|
| Purchase | Shop, order number (letters, digits, spaces and `. _ / # -`), order date, currency, shop total, tax and shipping (details), payment method, receipt or invoice (a vault document id the person can open), expected delivery, delivery date, an **address reference** (a label such as Home or Office, never the address itself), warranty end, return window end, status, source (web or iPhone), needs review (set when it came from a shared message), notes, archived, version, and the read copy of its payment link. |
| Item | Description, SKU, quantity, unit price, warranty end. An edit keeps an item's id, so a return keeps pointing at it; an item in a return cannot be removed or reduced below the returned quantity (`409 item_in_return`). |
| Event | The history, append-only: kind (`status`, `note`, `shipment`, `return`, `payment`, `refund`, `share`), the status before and after, the row it is about, a note, its source (`web`, `iphone`, `system`) and time. The database refuses every update. |
| Shipment | Carrier, tracking number (once per purchase), status (pending, in transit, out for delivery, delivered, failed, returned to sender), shipped, expected and delivered dates, note. Typed by the person or shared from the iPhone. The carrier and its public tracking link are detected on the server ([Courier tracking](#courier-tracking)). |
| Return | Status, request date, reason, note, the items and quantities (none: the whole order), the refund expected and its currency, the return deadline, pickup date, courier, tracking number, proof (a vault document id), the shop's confirmation (RMA), refund reference, the date it was sent back, expected and actual refund dates, dispute note, and the read copies of its refund links. |

## Lifecycle

A purchase moves only along its state machine (`TRANSITIONS` in `models.py`); a move it does not allow answers `409 invalid_transition` with the moves that are allowed, and a move from a stale version answers `409 stale`. Every move records an event with the state before and after, under a lock on the purchase row.

| From | To |
|---|---|
| ordered | confirmed, shipped, out for delivery, partly delivered, delivered, cancelled, disputed |
| confirmed | shipped, out for delivery, partly delivered, delivered, cancelled, disputed |
| shipped | out for delivery, partly delivered, delivered, cancelled, disputed |
| out for delivery | shipped (a failed attempt), partly delivered, delivered, disputed |
| partly delivered | shipped, out for delivery, delivered, return requested, disputed |
| delivered | return requested, refund pending, disputed |
| cancelled | refund pending, partly refunded, refunded, disputed |
| return requested | return approved, pickup scheduled, returned, delivered (withdrawn), refund pending, disputed |
| return approved | pickup scheduled, returned, delivered, refund pending, disputed |
| pickup scheduled | returned, return approved, disputed |
| returned | refund pending, partly refunded, refunded, disputed |
| refund pending | partly refunded, refunded, disputed |
| partly refunded | refunded, refund pending, return requested (another item), disputed |
| refunded | disputed |
| disputed | delivered, returned, refund pending, partly refunded, refunded, cancelled |

**Partly refunded** and **refunded** are never set by hand (`409 refund_from_finance`): they follow the refunds linked from Finance. A shipment marked out for delivery or delivered moves the purchase along (all shipments delivered: delivered; some: partly delivered). A return moves its purchase when the purchase's own machine allows it (requested, approved, pickup scheduled, returned, refund pending, disputed); rejecting or cancelling the only open return brings the purchase back to delivered.

A return has its own machine: requested, approved, pickup scheduled, sent back (`returned`, which records the date), refund pending, partly refunded and refunded (from the refund links), rejected, disputed (the note is kept as the dispute), cancelled.

## Payments and refunds

The purchase's **Details** have a **Payment** action and each return has **Link refund**:

- **Pick a payment.** `GET /api/v1/purchases/payments?kind=payment` lists the caller's own live transactions that are money spent (an expense debit, or money out that no rule recognised yet and is held as [uncategorized](finance.md#transaction-types); never income, a transfer or a refund) within 60 days before and 15 days after the order, ranked by closeness to the shop total and the date, with what is left to link to a purchase. Choosing one calls `finance.links.create` with dimension `purchase`, role `payment`, target `purchase`, and the whole remaining amount (or a given part; a payment in more than one currency needs `currency`). A purchase has **one** live payment link: the link's idempotency key names the purchase, and the `purchase` dimension is registered with `single_payment`, so the generic `POST /api/v1/finance/links` refuses a second live payment on it too (`409 target_linked`). Linking the same payment again answers `200 replayed`; a purchase linked to another payment answers `409 already_linked`; over the payment answers `409 over_allocated`; income or a refund answers `422 not_a_payment`.
- **Pick a refund.** `GET /api/v1/purchases/payments?kind=refund` lists the caller's transactions that give money back to a spending category (an expense credit, never an income posting) from 30 days before to 150 days after the return. `POST /api/v1/purchases/returns/{id}/refunds` links one with role `refund` and target `purchase_return` (the return of the original purchase); the read copy also records the purchase's own payment (`original_canonical_id`). A transaction recorded as income is refused (`422 not_a_refund`): correct it in Finance to a refund of the purchase's category first. The same rule holds on the generic `POST /api/v1/finance/links`: the module registers a `vet` with `finance.links`, which checks every new `purchase` link, from any route, under the owner's lock (a purchase takes role `payment` and money spent, a return takes role `refund` and money back to a spending category; `422 invalid_role`, `not_a_payment`, `not_a_refund`). A return's expected amount stays in the purchase's own currency and is never relabelled: a refund in the card's currency is compared at the rate the purchase's own payment was charged at (paid amount over the shop total), and with no such basis it counts as partly refunded. The default amount is the least of what is left on the transaction, on the return and on the purchase. **Refunds never add up to more than was paid** (the active payment link, else the shop total): checked before the link and again inside the link's own transaction under the owner's lock, so two tabs linking two refunds at once cannot both pass (`409 refund_over_paid` with `remaining`; tested with parallel requests in PostgreSQL). Several refunds per return and returns per purchase are allowed (partial and multi-item refunds).
- **Paid or refunded in cash.** The cash payment is recorded **once** in Finance through the same path as a hand-entered transaction (`finance.api.record_manual_transaction`, the journal's `manual` source, category shopping), then linked. A cash refund is recorded once as money back **in the purchase payment's category** (never income), then linked. The transaction id is derived from the purchase or return and the request's `Idempotency-Key`, so a retry finds the same transaction; an earlier one with other figures is never reused. If the link is refused (another tab linked first, or the refund would pass what was paid), the transaction this request recorded is reversed the same way as deleting it in Finance, so it never stays posted.
- **Remove link.** Voids the link (repeating it changes nothing). The transaction stays in Finance.

The purchase and the return keep **read copies** of their links (`payment`, `refunds`: link id, canonical id, status, amount, currency, who paid, recorded as cash). `finance.links` calls the purchase module's observer inside the link's own transaction whenever a link is made or changes:

| In Finance | The purchase or return |
|---|---|
| Linked | `active`: counts as paid, or as refunded |
| Corrected, same figures | unchanged, same canonical id |
| Corrected below the linked amount, or another currency | `needs_review`: not counted; the owner confirms or removes the link under Finance |
| Undone | `void` with `transaction_undone`: not counted; an undone refund puts the return and the purchase back to refund pending |
| Statement reconciled into a recorded payment | follows the link to the recorded payment's canonical id |
| Link removed by the owner | cleared |

The overview shows, per currency and never added across currencies: paid, refunded, paid less refunds, and refunds still owed (returns waiting for their refund). A purchase with a linked payment or refund cannot be deleted (`409 has_payments`): archive it to keep its history. A return with refunds cannot be deleted (`409 has_refunds`): mark it cancelled.

## Reminders

The daily retention job (`python -m anum_api.voice_retention`, reported under `purchases`) runs `purchases.jobs`: for each person with open purchases and **Reminders** on, one in-app notification (kind `purchase_reminder`, never a push or e-mail, since orders and addresses are personal data):

| Kind | When |
|---|---|
| `delivery_expected` | an undelivered purchase is expected today or tomorrow |
| `delivery_late` | its expected delivery date has passed |
| `return_window` | a delivered purchase without an open return reaches its return window end, or an open return its deadline, within the person's lead days (3 by default), at 1 day and on the day |
| `pickup_due` | a scheduled pickup is today or tomorrow |
| `refund_overdue` | a returned item's refund is past its expected date (14 days after it was sent back when no date was given) |
| `warranty` | the purchase's or an item's warranty ends within the person's warranty days (30 by default), at 7 days, 1 day and on the day |
| `missing_confirmation` | an order is still "ordered" 3 days later, or a return was sent back without the shop's confirmation |
| `proof_needed` | a return sent back (or waiting for its refund) has no proof in the vault. **Shown on the page's task queue only**: the reminder job skips it, so no notification is sent and no `purchase_reminders` row is written (no migration) |

Each step is sent once per due date (`purchase_reminders`, unique); a new date starts again; a refunded, cancelled or archived purchase, a closed return or turning reminders off stops them. A reminder whose notification fails is not marked, so the next run tries again. People are discovered as `anum_maintenance` (scope and status columns of open purchases only, never BYPASSRLS); the work runs as the application role in each person's own scope.

## iPhone share

Integration `purchases.share`, scope `purchases`, permission `purchase:write`, off until the owner turns it on under Settings, Integrations, iPhone ([iPhone integrations](iphone-integrations.md)), 10 requests a minute and 200 a day per device. `POST /api/v1/purchases/device/share` with the shared device token (`Authorization: Bearer <token>`, HTTPS only; without a device token the ordinary sign-in applies):

| Field | |
|---|---|
| `action` | `save` (default) or `test` (checks the connection and the reader, saves nothing) |
| `text` | The shared message, up to 4,000 characters |
| `idempotency_key` | Optional; otherwise the cleaned text is the key, so sharing the same message twice saves it once |
| `occurred_at` | When it happened |
| `ownership` | `personal` (default) or `business`, for a new purchase |

A share sent with a device token is recorded with source `iphone`; the same route called with the person's own sign-in (the web's **Import order**) is recorded with source `web`, on the purchase and on every history event it adds, so the Source filter tells them apart. ANUM **never reads the mailbox or the messages by itself**: only the text the person chose to share arrives. The exact Shortcut steps are on the iPhone page (`IPHONE_GUIDES`, `purchases.share`): a Share Sheet Shortcut for an order e-mail, and optionally a Messages automation for **one courier's sender**, never for every message.

**The shared text is data, never instructions** (`parser.py`): fixed patterns read an order number, a tracking number, a carrier and a shop from fixed lists, an amount and currency (a detail), an expected date and a delivery status. No model reads it, no link in it is opened, and nothing in it chooses what ANUM does. A status can only move a purchase **forward** on the delivery path (ordered, confirmed, shipped, out for delivery, delivered) where the state machine allows it; a message that says "cancelled" or "refunded" is only noted on the purchase, because a cancellation is the person's choice and a refund is their Finance transaction. The raw text is never stored. A message that matches a purchase by order number or tracking number updates it: a known tracking number moves that shipment on, and the purchase's status follows all of its shipments (one of two delivered is partly delivered); a new tracking number adds a shipment; the expected date is updated; otherwise a new purchase is saved with **needs review** on, for the person to check. Text with no order number, tracking number or known shop answers `422 nothing_found`, recorded as a failed event on the iPhone page.

### Save as purchase from the Email Center

In the Email Center's reader every message has **Save as purchase** (`purchases/mail_import.py`). The person picks whose purchase it is (theirs or the business's) and which attachment, if any, to keep as the receipt (PDFs first, then images; only attachments ANUM can download). `POST /api/v1/purchases/from-mail` (`account_id`, `folder`, `uid`, `attachment_index` or none, `ownership`) then:

- **Reads that one message with the person's own access**: `purchase:write`, `email:read` and read access to the mailbox (their own, or one shared with them at read or more); keeping the attachment also needs `document:write`. Each refusal names what is missing (`403 forbidden`, `mail_forbidden`, `vault_forbidden`; a missing mailbox or message `404`). The message is fetched without marking it read, and nothing is sent. ANUM still never reads a mailbox by itself.
- **Reads it as data** with the same fixed patterns as a shared message (the subject and the text): a message that names a purchase already saved (its order or tracking number) updates it as above; one that names a shop or an order saves a new purchase with **needs review**; one that names nothing still becomes a draft with **needs review**, named after the sender (its display name, else its domain), dated on the day the message was sent, with the subject as its note. A message that says "cancelled" or "refunded" is only noted.
- **Keeps the attachment** with the same checks as an upload (its type from the name and the declared type, the content signature, the malware-scan hook, the plan's storage) as a **private** receipt in the person's document vault, linked to the purchase (or reuses the person's own copy already in the vault). It becomes the purchase's receipt when the purchase has none and the person may edit it. Too large: `413` (save without it).
- **Once per message.** The key is derived from the person, the mailbox and the message's Message-ID (else the folder and number), so a second press, on any device, answers `200 replayed` with the first purchase. The history says "Saved from an email in the Email Center" and "Receipt kept from the email"; the audit row holds ids and the outcome only, never the shop, the subject, the sender or the file name.

The answer (`201`) carries `purchase_id` and a sentence; the reader links to the purchase (`#purchases?open=<id>` opens it in the drawer). On the phone the mail reader has the same action (`apps/mobile/lib/features/mail_center/`).

**A delivery message is never finance.** This route touches no ledger table and never reaches the iPhone SMS review queue (`docs/finance-sms.md`); the tests check that the finance messages, entries and transactions stay empty.

## Courier tracking

`anum_api/purchases/carriers.py`, read only, on every shipment answer (`tracking`) and on `GET /shipments/{id}/tracking`:

- **Carrier detection.** The carrier name the person typed or a mail named wins (English and Arabic names: Aramex, DHL Express, FedEx, UPS, SMSA Express, Emirates Post, Saudi Post (SPL)). Without a known name, the tracking number's own format decides, after removing spaces and dashes: UPS `1Z` plus 16 letters or digits; a UPU S10 item (two letters, eight digits, a check digit that must be right, then `AE` for Emirates Post or `SA` for Saudi Post); DHL `JJD...` piece numbers; 15, 20 or 22 digits for FedEx; 12 digits with FedEx's check digit; 10 digits with DHL Express's check digit (the first nine modulo 7). A format only one carrier uses with a passing check names that carrier (`detected_from: "number"`). A format several carriers share (ten digits are DHL Express and Aramex; twelve are FedEx, Aramex and SMSA) gives every candidate with its own link (`candidates`) and names none: the person opens the one that sent it. Another country's post and a wrong check digit give no candidate.
- **Deep links.** Each carrier's public tracking page with the cleaned number (8 to 40 letters and digits) as the only variable part. ANUM never opens the page or reads anything from it.
- **Status provider (off).** Reading a carrier's status from its public page is not reliable (pages change, block automated reads and differ by country), so it is not done. Instead `StatusProvider` (`supports(carrier)`, `status(carrier, number)` returning a bounded `TrackingStatus`: state, description, check time, at most 50 events) is the one place a tracking data provider plugs in. It is off until the operator sets `ANUM_PURCHASES_TRACKING_PROVIDER` (a name registered in `carriers.PROVIDERS`) and `ANUM_PURCHASES_TRACKING_API_KEY` (a secret, never in a ConfigMap); `status_provider` then names it and the web shows **Check status**. The provider is called with no database unit open; a carrier it does not cover or a failure answers `status_error` in plain words, and the carrier's link stays the way to follow it.

### Adding a status provider

A provider is a small module under `anum_api/purchases/` that implements `StatusProvider`, maps the vendor's states onto `in_transit`, `out_for_delivery`, `delivered`, `exception` or `unknown`, cleans every text it returns (plain text, bounded), raises `ProviderError` with a plain sentence (never the key or the vendor's raw body), and registers a factory in `PROVIDERS` under its name. It needs tests with a fake HTTP server, a privacy review (the tracking number leaves ANUM for the vendor) and an owner decision on the vendor and its cost. None is registered today.

## API

| Route | |
|---|---|
| `GET /api/v1/purchases` (`q`, `status`: a status or `awaiting`, `returns`, `open`, `closed`; `merchant` (exact shop, any case), `from` and `to` (order dates, inclusive; `422 bad_range` when reversed), `payment` (a method, `linked` or `unlinked`), `source` (`web`, `iphone`), `warranty=true` (the purchase's or one of its items' warranty ends today or later); `archived`, paging) | `purchase:read`. Each row also carries `first_item`, `item_count` and `latest_shipment` for the tracker |
| `GET /api/v1/purchases/merchants` | `purchase:read`; the shops of the caller's visible, unarchived purchases (at most 200), for the Seller filter. Another member's personal shops never appear |
| `POST /api/v1/purchases` (`Idempotency-Key`) | `purchase:write`; items and a first status (ordered to delivered) |
| `GET`, `PATCH /api/v1/purchases/{id}` (`version`, `items` replace the lines), `POST /{id}/archive`, `DELETE /{id}` | read; change needs the purchase's owner or `purchase:manage` |
| `POST /api/v1/purchases/{id}/status` (`to`, `version`, `note`, `occurred_at`) | the state machine; `GET /{id}/events` (paging), `POST /{id}/notes` |
| `POST /{id}/shipments`, `PATCH`, `DELETE /api/v1/purchases/shipments/{id}` | shipments; each answers its `tracking` (carrier, link, candidates, `status_provider`) |
| `GET /api/v1/purchases/shipments/{id}/tracking` | the shipment's carrier and links, and its `status` from the configured provider (`null` while off, `status_error` when the provider cannot answer); `purchase:read` and the purchase's own visibility |
| `POST /{id}/returns` (`Idempotency-Key`), `PATCH`, `DELETE /api/v1/purchases/returns/{id}`, `POST /returns/{id}/status` | returns; `GET /api/v1/purchases/returns` (`open`, paging) lists them across purchases |
| `GET /api/v1/purchases/overview`, `/reminders`, `GET`, `PUT /preferences` | figures per currency, what is due, the person's settings (reminders, lead and warranty days, default currency). The overview also has `month`, `orders_this_month` (not cancelled), `committed_this_month` (the shops' totals per currency, a detail and never the ledger), `untotalled_this_month` and `in_transit` (shipments in transit or out for delivery of purchases still awaiting delivery) |
| `GET /api/v1/purchases/payments` (`kind`, `q`, `around`, `amount`), `POST /{id}/payment`, `/payment/cash`, `DELETE /{id}/payment` | plus `finance:manage`; the caller's own transactions only |
| `POST /returns/{id}/refunds`, `/refunds/cash`, `DELETE /returns/{id}/refunds/{link_id}` | plus `finance:manage` |
| `GET /api/v1/purchases/export.csv` (`archived`) | every visible purchase; cells that start with `=`, `+`, `-` or `@` are escaped |
| `POST /api/v1/purchases/device/share` | the iPhone (above) |
| `POST /api/v1/purchases/from-mail` | Save as purchase from the Email Center (above): `purchase:write`, `email:read`, mailbox read access, plus `document:write` for the attachment |

Refusals answer `{"detail": "<plain words>", "code": "<code>"}` with figures where they help. Audit rows hold ids and codes only, never a shop, an order number, an address or an amount.

## Web

Sidebar **Purchases and Returns** (`#purchases`, `#purchases-list`, `#purchases-deliveries`, `#purchases-returns`, `#purchases-warranty`, `#purchases-settings`), built on the shared shell ([Finance shell](finance.md#finance-shell)) to the owner's mockup of 5 Nov 2026 (`PurchasesView.tsx`, `PurchaseTracker.tsx`):

- **Page head:** **Import order** (paste an order confirmation or delivery message; it goes to `POST /api/v1/purchases/device/share` with the person's own sign-in and an idempotency key, read by the same fixed patterns as the iPhone Shortcut, never a model, and a new purchase is marked for review), **Export** (CSV; with **Show archived** on under All purchases it exports the archived ones) and **Track purchase** (the purchase form).
- **Overview:** four KPI cards from `GET /overview` (orders this month, committed this month per currency, deliveries in transit with late ones, open returns with refunds still owed); the filter bar (search, Seller from `GET /merchants`, Status, Date: this month, last 30 or 90 days, this year; Payment: linked, not linked or a method; Source: web or iPhone); the **purchase tracker** (newest five, item and seller, order date, expected or delivered, payment method with its Finance link state, status chip, return deadline, total, a row menu); **Delivery and refund tasks** (the `due` list: delivery late or expected, proof needed, return window closing, pickup due, refund overdue, a missing confirmation, warranties within 7 days; each with its due date and one action that opens the order); and the note that refunds reduce what a purchase cost and are never income.
- **All purchases** (the same table with paging and archived), **Deliveries** (purchases awaiting delivery with carrier, masked tracking number, latest shipment status and expected date), **Returns and refunds** (open or all returns, reason, status, refund match, expected and refunded amounts), **Warranty** (`warranty=true`) and **Settings** (reminders, lead and warranty days, default currency, the iPhone Shortcut).
- **The order drawer** (beside the table from 1280 px, a sheet over the page below): total, payment method, the Finance transaction link state, the return reason, the ordered, shipped, delivered and refund steps, the return pickup, the proof photo and receipt as vault documents (each opens `#vault-documents?doc=<id>` when the person can see it), refund details with their match state (matched in Finance, check in Finance, awaiting refund), **Add carrier update** (a new shipment or the newest one the person may edit), **Upload proof** / **Upload receipt** (uploaded privately to the vault, then attached to the return or the purchase), **Link refund** (the refund picker; only the purchase's owner), the shipping tracking with the tracking number masked to its last four (a copy button copies it whole), **View on <carrier>** as a plain external link to the carrier's own public page (the server's detection, [Courier tracking](#courier-tracking); a number several carriers use lists each one), **Check status** only while a tracking provider is configured, links and records, and the newest history. **Open the full record** opens the purchase's page: **Delivery** (status along the machine, shipments), **Items**, **Returns and refunds**, **History** (with notes) and **Details** (edit, payment, archive, delete).

**No page scraping:** a status, an expected date or a "latest update" is what the person typed or shared, or what a configured tracking provider answered; ANUM never reads a carrier's site and never invents a scan. Every page handles loading, empty with a next action, errors with Try again, the viewer's permission refusal and paging; English and Arabic, right to left.

## Mobile

Menu, **Purchases** (`/purchases`, `apps/mobile/lib/features/purchases/`, [Record modules](mobile.md#record-modules)): the list with search, the status groups (all, open, awaiting delivery, returns, closed), paging and what is due; a purchase with its status and the moves the server allows (sent with the version on screen; a stale version reloads and says so), its items, shipments with carrier and tracking number (and **Track on <carrier>** buttons that open the carrier's public page from the server's detection, one per carrier when the number's format is shared; only https links are offered), returns, and the timeline (paged `GET /{id}/events`). Adding a purchase, a shipment and **Start a return** (reason, details, amount, return-by date) are real writes; the purchase and the return send an `Idempotency-Key`. **Link the payment** and, on a return, **Link the refund** pick one of the person's own Finance transactions (`GET /payments?kind=payment|refund`); a refund is never income. People who may not change a purchase see why. From 840 px the list and the purchase sit side by side. Tests: `apps/mobile/test/purchases/purchases_test.dart`.

## Storage, privacy and deletion

Migration `0136_purchases` (expand only, after `0135_vehicles`): `purchases`, `purchase_items`, `purchase_events`, `purchase_shipments`, `purchase_returns`, `purchase_reminders`, `purchase_preferences`. Every table has forced RLS with the tenant policy, the restrictive `purchase_visible_<table>` (business rows to the workspace, personal rows to their owner; the child rows carry the purchase's visibility and owner, copied and checked by the trigger `purchase_child_matches`, so no policy looks another table up), the restrictive `purchase_insert_<table>` (a row is written by the person it names) and `lifecycle_purge_<table>`. `purchase_events_append_only` refuses every update of the history. All are listed in `tenant_lifecycle_tables.py`.

The privacy export includes the person's own purchases (with their items, events, shipments, returns and reminders), the status changes, shipments and returns they made on business purchases, and their settings. **Delete my data** removes their personal purchases with every row, their own rows on business purchases, their reminders and settings; business purchases they added stay with the business, with their items. Finance links are removed by Finance's own deletion.

## Tests

`tests/test_purchases_tracking.py` (carrier detection by name and number format with check digits, shared formats listing every candidate, links with only the cleaned number, the tracking route without and with a fake provider, a provider that does not cover a carrier or fails, permissions and personal visibility), `tests/test_purchases_api.py` (rules, permissions, idempotency, the state machine and its history, shipments, the acceptance cases: Apple Pay plus SMS stays one expense after linking and allocating, a partial refund links to the original purchase and transaction and is not income, an income transaction is refused as a refund, refunds never pass what was paid, relinking and unlinking are idempotent, a correction keeps the link, an undo voids it, a manual cash purchase creates exactly one ledger transaction and a refused link undoes it, the generic link route keeps one payment per purchase, reminders once and stopped after close, the iPhone share with a scoped token, injection text that cannot cancel or refund, nothing reaching Finance), `tests/test_postgres_purchases.py` (forced RLS and visibility, append-only history, payment and partial refund add no posting and follow undo under RLS, concurrent refunds within what was paid, cash under RLS, reminder discovery, privacy delete), `tests/test_purchases_from_mail.py` (Save as purchase against the Email Center's fake mail server: a draft with its order and tracking number and a private receipt linked in the vault, a message without an order named after its sender, the message not marked read, once per message, permissions, a missing message, mailbox or attachment saving nothing, injected text only noted, the audit without content), web `test/purchases.test.ts`, `e2e/purchases.spec.ts` and `e2e/email-save-purchase.spec.ts`.

## Not built here

- On the phone: editing items, moving a return through its statuses, recording cash, notes, archiving and the CSV export (on the web).
- A tracking status provider: the interface is built and off ([Courier tracking](#courier-tracking)); no provider is contracted or registered, so statuses are typed or shared and the links open the carriers' own pages. The tracking links were written from the carriers' public sites and not opened from the build machine (their hosts are blocked there); check each once on a real shipment.
- The tracker's row menu has no bulk actions, and the drawer's **Edit** edits the purchase's details only (items and returns on the full record).
- The mockup's "linked card" name in the Payment column: the column shows the payment method and whether a Finance transaction is linked; the account name stays in Finance (personal banking stays private on business purchases).
- Automatic matching of a scanned receipt to its purchase or payment (the document vault's OCR fills the receipt; linking stays a person's choice).
- Store credit or gift card refunds that are not money in the ledger: note them on the return and archive the purchase.
