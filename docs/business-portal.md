# Business: the customer portal

The portal is a customer-facing view of one client account in the owner's Business workspace ([Business](business.md), roadmap P2). A customer opens a link the owner gave them and sees the offers sent to them, the change orders waiting for them, their invoices and their support requests; they can approve or decline an offer or a change order and open a support request. It is not a second ANUM login and it never gives a customer a workspace role.

- API: `services/api/anum_api/business/portal.py` (the customer's routes, `/api/v1/portal`), `portal_auth.py` (the link and its checks) and `api_portal_admin.py` (the owner's routes, `/api/v1/business/portal`).
- Storage: migration `0089_business_invoices_portal` (`business_portal_access`, `business_portal_decisions`, with the invoice tables), memory store for local runs and tests.
- Web: the public page `/portal` (`apps/web/src/PortalApp.tsx`), reached by the link, English and Arabic; the owner manages links in Business, Accounts and contacts.
- Flutter: the customer portal screen on the phone (`apps/mobile/lib/features/portal/`), opened from the sign-in screen with the same link, English and Arabic; the owner's invoice screens are in Business (`apps/mobile/lib/features/business/invoices.dart`).
- Contracts: `packages/contracts/src/business.ts` (`Portal*` types).

## Access model

A customer is not a member of the workspace. Instead an owner creates an access link for one contact of one client account (`POST /api/v1/business/portal/access`, owners only):

- The link is `/portal#access=<token>`. The token is `anum_portal.<tenant id>.<workspace id>.<access id>.<secret>`: the first three parts are identifiers, not secrets; the secret is 256 random bits. Only the SHA-256 of the secret is stored, so a database read cannot rebuild a link, and the link is shown once, when it is created.
- It travels in the URL fragment, which browsers never send to a server, so it does not reach access logs or `Referer` headers. The page moves it into memory and removes it from the address bar. Every API call sends it as `Authorization: Bearer <token>`.
- It expires (1 to 365 days, 90 by default) and an owner can turn it off at any time. An account can hold at most 10 active links. A link can be read-only (`can_decide` false): the customer then reads everything and approves nothing.
- A link is refused when its account or contact is marked do not contact, when either was deleted, and when the secret is wrong. An unknown link, a wrong secret and a link for another workspace all get the same answer, so a guessed link reveals nothing.
- The link finds its rows inside its own tenant and workspace under forced row-level security (the same `anum.tenant_id` and `anum.workspace_id` the rest of Business uses; no role bypasses it). A link whose tenant or workspace part is changed finds no access row and fails. After that, the portal code reads only rows of the one client account: every query names `account_id`, and a record of another account reads as missing (404), never as forbidden.
- Portal routes return explicit portal views, never internal records. A customer never receives internal cost, margin, assumptions, notes, assignees, SLA targets, approval records, user ids, tenant or workspace ids, or other accounts.
- Everything a customer does is audited under the actor `portal:<access id>` with ids and counts only; a decision or a request also lands in the account's activity trail.
- An archived workspace is read-only for customers too: they can still read, but approving and opening requests are refused with a plain message.
- A customer can open at most 5 support requests a day per link.
- Browsers send the link only to this API (CORS allows the `Authorization` header and never credentials); the API's global rate limit applies to portal routes like every other route.

## What the customer sees

| Section | What is shown | What they can do |
|---|---|---|
| Offers | Proposals that were sent for acceptance (not drafts, not withdrawn): summary, scope, deliverables, exclusions, assumptions, acceptance criteria, lines, milestones, tax, total, payment terms, validity | Approve or decline the exact version shown (typed name, optional note) |
| Change orders | Change orders waiting for approval and approved ones: the request, added and removed lines, the price and timeline impact | Approve or decline the exact change order shown |
| Invoices | Invoices that were sent (open, paid) and voided ones marked void: lines, tax, total, payments received, how to pay | Open the printable copy (Save as PDF) |
| Requests | Their account's support requests: status, their text, the resolution, replies that were actually sent to them | Open a new request (title, details, severity low to high, optionally a project) |

There is no online payment: ANUM collects no money. The invoice shows the owner's bank details and the customer pays outside ANUM; the owner records the payment ([invoices](business.md#invoices)).

## Decisions

A customer's approval is a record for the owner, not the acceptance itself.

1. The owner sends a proposal for acceptance (`request-acceptance`) or a change order for approval (`submit`), as before.
2. The customer opens it in the portal and approves or declines. The request carries the content hash they were shown; if the proposal or change order changed since (a new version, a new contract version), the API refuses with 409 and the page reloads the latest text. The decision is stored once per exact version (a unique index backs it): repeating it is harmless, reversing it is refused ("contact the business to change it").
3. The owner sees "Approved by <name> in the portal on <date>" (or declined, with the note) on the proposal and on the change order, flagged as current when it is about the version waiting. The existing owner approval does the real work: `business.accept_proposal` in Approvals (the payload hash the approver sees) and a change order's approve button, which creates the new locked proposal version. A customer decision never accepts a proposal or approves a change order by itself, and a missing customer decision never blocks the owner.
4. An offer past its `valid_until` date cannot be approved; a change order made stale by a newer contract version cannot be approved until it is sent again.

## Giving access

Owners: Business, Accounts and contacts, a contact's "Customer portal" block. Creating a link shows it once with a copy button and a reminder that it is a credential: send it only to that person. Members can see who has access and when each link was last used (`last_used_at`, refreshed at most every 10 minutes) but cannot create or turn off a link.

| Route | Who | Does |
|---|---|---|
| `GET /api/v1/business/portal/access?account_id=` | owner, member | The account's links (no token, no hash) |
| `POST /api/v1/business/portal/access` | owner | Creates a link; the response carries `token` and `portal_path` once |
| `POST /api/v1/business/portal/access/{id}/revoke` | owner | Turns a link off (idempotent) |
| `GET /api/v1/business/portal/decisions?account_id=&subject_id=` | owner, member | What customers decided, newest first |

| Customer route (`/api/v1/portal`) | Does |
|---|---|
| `GET /me` | Business name, account, contact, counts of what needs them, the account's open projects (for the request form) |
| `GET /offers`, `GET /offers/{id}`, `POST /offers/{id}/decision` | Offers and the decision |
| `GET /change-orders`, `GET /change-orders/{id}`, `POST /change-orders/{id}/decision` | Change orders and the decision |
| `GET /invoices`, `GET /invoices/{id}`, `GET /invoices/{id}/print?lang=en\|ar` | Invoices and the printable copy |
| `GET /tickets`, `GET /tickets/{id}`, `POST /tickets` | Requests |

## Storage

Migration `0089_business_invoices_portal` creates `business_portal_access` (the contact's link: SHA-256 of the secret, expiry, revocation, last use) and `business_portal_decisions` (one decision per exact version) under forced row-level security with scoped composite foreign keys, plus the purge grants and policies of organisation and workspace deletion ([lifecycle](organisation-lifecycle.md)). `token_hash` is never exported. Expand only.

## Tests

- `services/api/tests/test_business_portal.py`: the link (minting, parsing, hashing, shown once, never stored), owner-only creation and revocation, expiry and revocation, forged and foreign links failing with one answer, a do-not-contact contact, the limit of active links, isolation between client accounts, workspaces and tenants, no internal field in any response, decisions bound to the hash (repeat, reverse, stale, expired, read-only link), change orders, invoices and their printable copy, requests (rate limit, drafts hidden), the archived workspace and the audit walk.
- `services/api/tests/test_postgres_business.py::test_invoices_and_the_customer_portal_under_rls`: the same path as the app role under forced RLS, forged tenant and workspace parts, and the frozen invoice trigger.
- `apps/web/test/businessInvoices.test.ts`, `apps/web/test/portal.test.ts` and the Playwright specs `apps/web/e2e/business-invoices.spec.ts` and `apps/web/e2e/portal.spec.ts`; `apps/mobile/test/portal_test.dart` and `apps/mobile/test/business_invoices_test.dart`.

## Not in this release

- Online payment and payment reminders: ANUM collects no money; the owner records payments by hand.
- A customer replying inside a request (they open a new request or answer the e-mail they received), file attachments, a public status page and e-signature of a certified kind (the typed name and the stored hash are the record).
- Notifications to the customer: nothing is sent. The owner shares the link and, for an invoice, the printed copy, through the inbox's own approved path.
- Customer sign-in with a password, SSO or one-time codes: a link is the credential; a code sent by e-mail is a later step.
