# Business: the customer portal

The portal is a customer-facing view of one client account in the owner's Business workspace ([Business](business.md), roadmap P2). A customer opens a link the owner gave them and sees the offers sent to them, the change orders waiting for them, their invoices and their support requests; they can approve or decline an offer or a change order, open a support request and write inside one. It is not a second ANUM login and it never gives a customer a workspace role.

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
- A customer can open at most 5 support requests a day per link, and write at most 20 messages a day inside their requests.
- Browsers send the link only to this API (CORS allows the `Authorization` header and never credentials); the API's global rate limit applies to portal routes like every other route.

## What the customer sees

| Section | What is shown | What they can do |
|---|---|---|
| Offers | Proposals that were sent for acceptance (not drafts, not withdrawn): summary, scope, deliverables, exclusions, assumptions, acceptance criteria, lines, milestones, tax, total, payment terms, validity | Approve or decline the exact version shown (typed name, optional note) |
| Change orders | Change orders waiting for approval and approved ones: the request, added and removed lines, the price and timeline impact | Approve or decline the exact change order shown |
| Invoices | Invoices that were sent (open, paid) and voided ones marked void: lines, tax, total, payments received, credit notes, reports they made, how to pay | Open the printable copy, download the PDF, report a payment they made |
| Requests | Their account's support requests: status, their text, the resolution, replies that were actually sent to them, and the conversation inside the request | Open a new request (title, details, severity low to high, optionally a project); write inside a request ([below](#replying-inside-a-request)) |

There is no online payment: ANUM collects no money and has no payment gateway. The invoice shows the owner's bank details and the customer pays outside ANUM; the business records the payment ([invoices](business.md#invoices)), or confirms the customer's report of it ([below](#reporting-a-payment)).

### Paying by card

When the business made a Moyasar payment link for the invoice's current balance ([Business: payment links](business.md#payment-links)), the invoice carries `payment_url`, the PDF prints it, and the customer pays by card, mada, Apple Pay or STC Pay on Moyasar's page. The payment is recorded on the invoice when Moyasar reports it to ANUM; the customer does not report it.

## Reporting a payment

Where the customer pays by bank transfer, cash or cheque, the invoice page has **I have paid** (only for a sent invoice with a balance, on a link that may act, in a workspace that is not archived). The customer gives their name, the amount, the date, the method and a reference (a transfer number) and sends it.

- It is a **report, not a payment**. The balance, the status and the milestones do not change. The page says "Waiting for the business to confirm" and lists the report with its state. Nothing a customer types can mark an invoice paid.
- The business sees it on the invoice (and as a count on the Invoices list) and confirms it after checking its bank, which records an ordinary payment, or rejects it with a reason the customer then sees ([payment notices](business.md#payment-notices-from-the-portal)).
- Checks: the amount is above zero and not above the balance; the date is not in the future and not before the issue date; a name of at least two characters; every submit carries an `idempotency_key` the form made up (a UUID, regenerated each time the form opens, kept across a retry of the same submit): the same key again returns the existing report with 200, while two separate payments of the same amount on the same day are two reports (a unique index per invoice backs it); at most five reports wait on one invoice and together they cannot exceed the balance. A read-only link (`can_decide` off) and an archived workspace refuse with the same sentences as decisions. Text is cleaned (spaces folded, long dashes replaced) and shown as plain text only.
- Audit: `business.portal.payment_reported` (ids and amount, never the typed text) and an activity on the account.

What this is not: a gateway. A card or online bank payment would need a provider, merchant keys, signed webhooks and reconciliation, and is a separate owner decision.

## Replying inside a request

A request's page has a **Conversation**: what the customer wrote there and what the team answered there, oldest first (`messages`, each `you` or `business`; the team signs as the business's legal name, never a person or a user id). The customer writes with `POST /tickets/{id}/messages {body, idempotency_key}`:

- Only in their own account's requests (another account's request reads as missing, 404). A closed request takes no more messages (409, "open a new request"); a request that waited on the customer moves back to in progress (the SLA pause ends); a resolved one opens again (counted as a reopen, its resolution cleared). A read-only link may write: a message is not an approval.
- The form makes up a key per submit (a UUID kept across a retry): the same key again returns the request with 200 and adds nothing (a unique index per request backs it). At most 20 messages a day per link (429). Text is cleaned (long dashes replaced, control characters dropped, line breaks kept, at most 4,000 characters) and shown as plain text only. An archived workspace refuses with the paused sentence and the page shows "you can read but not write now" (`can_reply` false).
- The team sees it in the ticket ([Business: replies and approval](business.md#replies-and-approval)) with an unread count, and answers there; the answer appears in the customer's portal. Nothing is delivered by email or WhatsApp in either direction, so neither side needs an approval; an e-mail or WhatsApp reply keeps its own approval path.
- Audit: `business.portal.ticket_message` (ids only, never the text) and an activity on the account (`portal_ticket_message`, or `ticket_reopened`); the team's answer is `business.ticket.portal_answer`.

Web: `PortalApp.tsx` (the request page) and `BusinessSupport.tsx` (the ticket drawer). Phone: `apps/mobile/lib/features/portal/portal_screens.dart` (the request screen) and `apps/mobile/lib/features/business/support_screen.dart` (the ticket). Storage: `business_ticket_messages`, migration `0175_business_depth` (forced RLS, a scoped foreign key to the ticket that removes its messages with it, the purge grants).

## Decisions

A customer's approval is a record for the owner, not the acceptance itself.

1. The owner sends a proposal for acceptance (`request-acceptance`) or a change order for approval (`submit`), as before.
2. The customer opens it in the portal and approves or declines. The request carries the content hash they were shown; if the proposal or change order changed since (a new version, a new contract version), the API refuses with 409 and the page reloads the latest text. The decision is stored once per exact version (a unique index backs it): repeating it is harmless, reversing it is refused ("contact the business to change it").
3. The owner sees "Approved by <name> in the portal on <date>" (or declined, with the note) on the proposal and on the change order, flagged as current when it is about the version waiting. The existing owner approval does the real work: `business.accept_proposal` in Approvals (the payload hash the approver sees) and a change order's approve button, which creates the new locked proposal version. A customer decision never accepts a proposal or approves a change order by itself, and a missing customer decision never blocks the owner.
4. An offer past its `valid_until` date cannot be approved; a change order made stale by a newer contract version cannot be approved until it is sent again.

## Giving access

Owners: Business, **Accounts** tab: pick a client account and use its "Customer portal" block (the same block appears in a lead's drawer). Creating a link shows it once with a copy button and a reminder that it is a credential: send it only to that person. Members can see who has access and when each link was last used (`last_used_at`, refreshed at most every 10 minutes) but cannot create or turn off a link.

| Route | Who | Does |
|---|---|---|
| `GET /api/v1/business/portal/access?account_id=` | owner, member | The account's links (no token, no hash) |
| `POST /api/v1/business/portal/access` | owner | Creates a link; the response carries `token` and `portal_path` once |
| `POST /api/v1/business/portal/access/{id}/revoke` | owner | Turns a link off (idempotent) |
| `GET /api/v1/business/portal/decisions?account_id=&subject_id=` | owner, member | What customers decided, newest first (the newest 100). The account's "Customer portal" block lists them under **What this customer decided** (approved or declined, proposal or change order and its version, who signed, when, and the note), ten a page with the shared Pager, with a line when the 100 limit is reached. |

| Customer route (`/api/v1/portal`) | Does |
|---|---|
| `GET /me` | Business name, account, contact, counts of what needs them, the account's open projects (for the request form) |
| `GET /offers`, `GET /offers/{id}`, `POST /offers/{id}/decision` | Offers and the decision |
| `GET /change-orders`, `GET /change-orders/{id}`, `POST /change-orders/{id}/decision` | Change orders and the decision |
| `GET /invoices`, `GET /invoices/{id}`, `GET /invoices/{id}/print?lang=en\|ar`, `GET /invoices/{id}/pdf` | Invoices, the printable copy and the PDF file |
| `POST /invoices/{id}/payment-notices` | Report a payment (body: `payer_name`, `amount`, `paid_on`, `method`, `reference`, `note`); 201 with the invoice, 200 when the same report already waits |
| `GET /tickets`, `GET /tickets/{id}`, `POST /tickets` | Requests |
| `POST /tickets/{id}/messages` | Write inside a request (body: `body`, `idempotency_key`); 201 with the request, 200 when the same key was sent before |

## Storage

Migration `0089_business_invoices_portal` creates `business_portal_access` (the contact's link: SHA-256 of the secret, expiry, revocation, last use) and `business_portal_decisions` (one decision per exact version) under forced row-level security with scoped composite foreign keys, plus the purge grants and policies of organisation and workspace deletion ([lifecycle](organisation-lifecycle.md)). `token_hash` is never exported. Expand only.

## Tests

- `services/api/tests/test_business_portal.py`: the link (minting, parsing, hashing, shown once, never stored), owner-only creation and revocation, expiry and revocation, forged and foreign links failing with one answer, a do-not-contact contact, the limit of active links, isolation between client accounts, workspaces and tenants, no internal field in any response, decisions bound to the hash (repeat, reverse, stale, expired, read-only link), change orders, invoices and their printable copy, requests (rate limit, drafts hidden), the archived workspace and the audit walk.
- `services/api/tests/test_business_portal.py` also covers reporting a payment (nothing changes until confirmed, rejection reasons, repeats, amount and date checks, the cap, cleaned text, read-only and archived refusals, paid, void, draft and foreign invoices), credit notes in the customer's view and the PDF.
- `services/api/tests/test_postgres_business.py::test_invoices_and_the_customer_portal_under_rls`: the same path as the app role under forced RLS, forged tenant and workspace parts, and the frozen invoice trigger.
- `apps/web/test/businessInvoices.test.ts`, `apps/web/test/portal.test.ts` and the Playwright specs `apps/web/e2e/business-invoices.spec.ts` and `apps/web/e2e/portal.spec.ts`; `apps/mobile/test/portal_test.dart` and `apps/mobile/test/business_invoices_test.dart`.
- `services/api/tests/test_business_portal_replies.py`: writing inside a request and answering it (the conversation on both sides, a waiting request moving on, a resolved one reopening, a closed one shut, the same key once, cleaned text, the daily limit, another account's request, permissions and scope, an active link required for an answer, read marks, the archived workspace, the audit without the text); `test_postgres_business_depth.py` under forced RLS; `apps/web/e2e/portal.spec.ts` and `apps/web/e2e/businessSupport.spec.ts`; `apps/mobile/test/portal_test.dart` and `apps/mobile/test/business_support_test.dart`.

## Not in this release

- Online payment (a gateway) and payment reminders: ANUM collects no money; the business records payments by hand or confirms a customer's report. An Arabic PDF is not made (Latin-only fonts); the printable page covers Arabic.
- File attachments in a request, a public status page and e-signature of a certified kind (the typed name and the stored hash are the record).
- Notifications to the customer: nothing is sent. The owner shares the link and, for an invoice, the printed copy, through the inbox's own approved path.
- Customer sign-in with a password, SSO or one-time codes: a link is the credential; a code sent by e-mail is a later step.
