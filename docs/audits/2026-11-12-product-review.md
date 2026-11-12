# ANUM OS: an honest product and commercial review

Reviewed 12 November 2026, read only, from the docs and the code and tests themselves.

## The short version

ANUM is a very large, carefully engineered system with almost no proof of real-world use. Size today: about 290,000 lines of API code, 208,000 lines of Flutter, 80,000+ lines of web, 177 database migrations, 500 API test files and 228 browser test specs. That is a 20-person company's product, built by one founder with AI in a few months.

Engineering habits are good (tenant isolation, approvals, audit, no faked compliance). The problem is **breadth with no customer**. Nearly every outside connection (Stripe, Moyasar, Brevo, noon, Twilio, WhatsApp, bank SMS, ZATCA) has only been tested against fakes. No second organisation has ever used it. The AI may still be on placeholder answers on the VM (`ANUM_MODEL_PROVIDER=mock` is an open owner check).

## 1. Does it solve real problems?

### (a) Personal use: yes, this is where it is strongest
- **Money in one place for a Gulf earner.** Saudi bank PDF statements (checked on the owner's two real ones), bank SMS and Apple Pay matched into one transaction, loans to and from people, salary detection, a cash runway and a weekly money note. Few products do this well for Saudi and UAE banks.
- **Life admin.** The document vault with expiry dates, vehicle costs and renewals, purchases and returns, and the calendar.
- **Weak spots.** Shortcuts and manual imports, no live bank feeds, other banks unverified.

Verdict: a powerful personal tool, not yet a product.

### (b) A small Gulf business or team: partly
- **Solved reasonably:** invoices with a customer portal, simple sales pipeline, books with P&L, VAT return figures for KSA and UAE, team tasks, approvals, shared email and calendar, attendance.
- **Solved poorly:**
  - No compliant e-invoicing. ZATCA Phase 2 deliberately refuses, and UAE e-invoicing through an accredited provider is not built.
  - No payroll or WPS.
  - No bank feeds.
  - Card payments are unproven.
  - It runs on one free VM with no SLA.
  - The interface is overwhelming: dozens of modules and an AI layer on top.

### (c) Sellable
Not as a general "AI operating system": nobody in the Gulf buys a horizontal OS from a one-person company with draft legal pages and no pentest.

What could sell is a **narrow, done-for-you service** run on ANUM. The owner's 17+ years in fleet operations point at fleet cost control.

## 2. Is there a sales scope?

### Realistic first customer
UAE and KSA fleet operators with 20 to 200 vehicles: transport, concrete and mixers, contractors and workshops. The buyer is an operations manager in the owner's network; this matches the portfolio site and Tyre Pulse.

### What they already use
- Excel and WhatsApp, which are the real competitor.
- Telematics portals: Samsara, Teltonika-based local trackers, Cartrack.
- Fleet software: Fleetio, plus local fleet ERPs.
- Odoo Fleet, or modules inside Zoho, Odoo or SAP Business One.

### Why they would switch, or not
- **Switch:** not for new software, but they will pay an expert who turns messy fuel, tyre and maintenance data into a monthly cost report with savings actions.
- **Not switch:** for software alone; Fleetio is mature and trackers bundle fleet features.

### Small business accounting is the wrong first market
Zoho Books, Wafeq, Qoyod and Daftra are cheap (roughly AED 50 to 300 a month, estimate) and already accepted by the tax authorities. Qoyod and Wafeq are ZATCA integrated. UAE e-invoicing will need an accredited service provider: large firms from 2027, others after. Check the current FTA timeline. Losing on compliance in accounting is fatal.

### Personal finance is also weak commercially
YNAB, Wallet by BudgetBakers and bank apps are free or cheap, and few consumers here pay for budgeting.

### Missing before anyone can be charged
1. The UAE trade licence on contracts and invoices; check its activity covers IT services.
2. Legal documents reviewed by a UAE lawyer. Today they are drafts with placeholders.
3. Payments: Stripe not opened, tested only against a fake. Bank transfer is enough for a pilot.
4. Data protection: a PDPL basis, a data processing agreement, and where data lives (Riyadh VM: fine for Saudi, cross-border for UAE).
5. Uptime: one free VM, no failover, trial credit ended about 10 November (4 OCPU / 24 GB may exceed the free amount), off-site backups only since 10 November. Promise no SLA.
6. Security: no pentest, no branch protection, and many merges skipped CI.
7. Onboarding: self sign-up needs the mail server and an invitation; fine hand-held, not self-serve.
8. VAT registration once revenue passes the threshold.

### The narrowest sellable offer (all prices are estimates)
**"Fleet Cost Control Pilot", 60 days, for one fleet.**
- **Deliverables:** import fuel, tyre, maintenance and purchase records; monthly cost per vehicle and per km, tyre forecasts, renewal calendar, and a review meeting with three to five savings actions.
- **Setup:** AED 3,000 to 6,000 (estimate). About 3 to 5 days of senior consultant time plus data clean-up.
- **Monthly after the pilot:** AED 1,000 to 2,500 (estimate) for 20 to 150 vehicles. Software alone is about USD 4 to 10 per vehicle per month (estimate); the premium is the expert review.
- **Delivery:** the owner operates ANUM; the client gets PDF and Excel reports. No multi-tenant onboarding or SLA needed.

## 3. Are the modules complete?

Verdicts come from code and tests. "Fakes" means outside services were simulated in tests.

| Module | Verdict | Evidence |
|---|---|---|
| Personal Finance | Usable with gaps | 84 API test files (25 against PostgreSQL), 27 browser specs. Checked on 2 real Saudi statements; other banks and SMS formats unverified; no bank feeds |
| Business books and VAT | Usable with gaps (owner only) | Deep ledger, assets, FX and VAT return. ZATCA Phase 2 refuses by design (`NotOnboarded`); no UAE accredited e-invoicing; never files |
| Business sales, invoices, portal | Usable with gaps | Pipeline, invoices, refunds and portal built. Stripe and Moyasar tested against fakes only; invoice and portal tests are mostly in-memory |
| Customer billing and Ready to sell | Built, unverified | Plans, proration and credit notes. No real payment yet; legal pages are drafts |
| Calendar | Usable with gaps | Recurrence and sync built; primary calendar only; the phone cannot connect an account |
| In-app team calls | Unverified | Built, but the TURN ports are not opened on the VM, so calls across networks are untested |
| Phone calls (Twilio) | Thin | No Twilio account; the phone stops before the provider SDK |
| Contacts | Usable with gaps | Import from CSV, Excel and vCard; no automatic merge |
| Email (OAuth and Email Center) | Usable with gaps | Large and working for the owner; Email Center behind a flag; its pentest not run |
| Files and Vault | Usable with gaps | Quotas and versions; malware scan and OCR off by default |
| Vehicles | Usable with gaps | Owner-grade; iPhone driving Shortcuts not tested on a device; no link to Tyre Pulse vehicles |
| Purchases | Usable with gaps | No courier tracking provider; phone editing limited |
| Attendance | Thin | Phase A only; phone and Shortcuts not checked on a real device |
| Tasks, Command, Agents, AI | Usable with gaps | Strong guardrails. Output quality unproven while the VM model may be `mock`; continuing durable steps needs NATS, which the VM does not run |
| Team, admin, governance | Complete in code, unverified in use | Best-engineered part, but never exercised by a second real organisation |
| Marketing (social, Brevo, WhatsApp) | Unverified | No accounts connected; planner tested with the mock model |
| Commerce (Amazon, noon) | Unverified | noon API paths guessed and tested against a fake server |
| Trading (IBKR) | Unverified, and a risk | Live trading is built but a paper account is not even set up |
| Tyre planning | Usable (owner's data) | Read-only over Tyre Pulse; report delivery by email and WhatsApp is missing |
| HQ 3D office ("ANUM MINE") | Breadth without depth | 61 of 228 browser specs, 31 API test files, about 60 docs. No customer value; it caused CI timeouts |
| Flutter phone app | Usable with gaps | 181 test files. Real-device checklist not run; many screens say "use the web" |
| Desktop (Tauri) | Thin | No signed installers (owner deferred) |
| Kotlin Android | Frozen | Should be deleted |

**Patterns to worry about**
- Every module has a "Not done yet" list; work moves on before the current one is proven.
- Integrations tested against fakes, so green CI says little about real behaviour.
- Phone parity with the web across 40+ feature folders doubles the cost of every change.

**Only the owner can unblock:** a real AI model key on the VM, Oracle billing or resize, branch protection, a way to receive payment, lawyer review and company facts, SPF and DNS fixes, provider accounts (Twilio, Meta, Brevo, Firebase), the pentest, and five real prospect conversations.

## 4. The 60-day focus

**Finish and sell one thing: the Fleet Cost Control Pilot, with ANUM as the owner's internal engine.**

### Finish
- **Vehicles plus Tyre planning plus Purchases:** a fleet vehicle list, cost per vehicle and per km, renewals, and tyre forecasts on client data imported from Excel.
- **One client-ready monthly report:** a PDF and Excel file in English and Arabic.
- **Business invoices:** for billing the pilot by bank transfer.
- **Real AI turned on:** for summaries in the report.
- **Basic reliability:** daily backups confirmed, and an uptime check.

### Commercial steps
- Run 10 conversations with fleet managers from the owner's network.
- Sign 2 paid pilots and get 1 written case study.
- Use a one-page contract and a data processing note reviewed once by a lawyer.

### Freeze (keep for the owner's own use, no new work)
Personal Finance depth, Email Center, Calendar sync, Marketing, Commerce, Calls, Attendance, Agents and governance depth, Customer billing and self-serve sign-up.

### Cut or park
HQ 3D office (out of CI), IBKR live trading, the Kotlin Android client, desktop releases, phone parity work, the Flutter 3.50 migration.

### Rule for the 60 days
No new module, migration or doc unless a paying pilot customer needs it.

If no fleet manager will pay for a pilot after 10 conversations, that is the real answer. In that case the right next move is to keep ANUM as the owner's personal tool, not to build more features.
