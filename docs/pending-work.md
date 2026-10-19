# Pending work (handoff, 18 October 2026)

State at handoff: every PR from the 18 October session is merged into `main` (#104, #105, #114, #115, #116, #117, #118, the Business section, #119, #121, #122, #123). Migration chain on `main` ends `0069_mail_writers_uidvalidity`, `0070_business`, `0071_calls`, `0072_push_device_language`, `0073_app_calls`, `0074_brevo_campaigns`. Nothing is open from that session except the older dependency PRs (#48, #50, #91 to #103), #10 and #110.

## Owner steps (only the owner can do these)

1. **Update the Oracle VM** to get everything above live: `cd ~/anum && sudo bash infra/oracle/update.sh`.
2. **Email Center** is on and `admin@anum360os.com` is connected. To bring the other domains in:
   - In Mailcow, check each domain's **DNS** button is all green; add aliases (for example `info@<domain>`) that go to `admin@anum360os.com`.
   - In `infra/oracle/.env` set `ANUM_MAILSERVER_ALLOWED_DOMAINS=anum360os.com,anum360.ae,tyrepulse.app,easyvoicequote.com,shahzebrahman.com`, then `cd ~/anum/infra/oracle && docker compose up -d api`.
   - In ANUM, Email, Mailbox settings, Send as: add each address.
3. **DNS fixes** (the owner changes DNS, never Claude):
   - `easyvoicequote.com`: SPF is `v=spf1 +all` (anyone may send as the domain). Change to `v=spf1 mx include:spf.brevo.com ~all`.
   - `anum360os.com`: SPF is `v=spf1 ~all`. Change to `v=spf1 mx ~all` (add `include:spf.brevo.com` if Brevo sends for it).
   - `anum360.ae` and `easyvoicequote.com`: MX points to Amazon inbound (`inbound-smtp.ap-northeast-1.amazonaws.com`). Decide whether anything uses it before moving MX to `mail.anum360os.com`. Keep the Resend records on `anum360.ae` (`send`, `resend._domainkey`).
   - `shahzebrahman.com`: mail is on Zoho. Keep Zoho, or copy mail with Mailcow Sync jobs before moving MX.
   - `tyrepulse.app`: not checked yet (`host -t MX tyrepulse.app`).
4. **Team calls** (`docs/in-app-calls.md`): open TCP 3478, UDP 3478, UDP 49160-49200 in the Oracle security list, then `bash infra/oracle/calls.sh enable`; test phone on mobile data to laptop on Wi-Fi.
5. **Phone calls through a provider** (`docs/calls.md#owner-steps`): Twilio account, number, API key and TwiML app, entered in Calls, Provider. Off until then; paid per minute.
6. **Brevo** (`docs/brevo.md`): paste the API key in Marketing, Email campaigns, Connection (never in chat); authenticate sending domains in Brevo (DKIM, brevo-code).
7. **Audit leftovers** (`docs/business/P0_CI_REMEDIATION.md`): staging smoke test, migration dry run and restore drill on the VM; turn on branch protection.
8. **Finance** (`docs/finance-trading-readiness.md`): choose market-data providers; live trading stays off.
9. **Codex review limit** is used up; reviews resume when the owner adds credits.
10. **Organisation export and deletion** (`docs/organisation-lifecycle.md`, migration `0079_tenant_lifecycle`): the new NOLOGIN role `anum_tenant_purger` must exist before that migration runs. The Oracle update does it (`db-init` runs `bootstrap-database.sql` first); on Render or Kubernetes re-run `infra/helm/bootstrap-database.sql` as the database admin before deploying.

## Build work still open (with reasons)

- **Team calls:** no real-device call yet; no CallKit/ConnectionService screens, group calls, video switch mid-call or screen sharing; signaling polls the database instead of the event bus.
- **Phone calls:** desktop app calls only through `tel:` (its CSP lacks the Twilio hosts); AI calls and provider setup are web only.
- **Brevo:** untested against a real Brevo account; no planner skill turns plain words ("send the winter newsletter") into the Brevo tools; webhooks, contacts and templates out of scope.
- **Email:** more than 1000 new messages between two background checks are not forwarded (the rule reports it).
- **Finance:** market-data fetch waits on the provider decision.
- **Business (roadmap P3 and later):** human-approved sending of outbound messages, online payment and credit notes for invoices, customers replying inside a portal request, a customer one-time code sign-in; per-agent cost caps and a general agent kill switch (gap register `docs/business/ANUM_BUSINESS_GAP_REGISTER.md`).
- **Email domains page in ANUM** (adding Mailcow domains from ANUM with approvals) was offered, not started.
- **"All inboxes" view**: built (docs/email.md#all-inboxes); each inbox is read live per page with an 8 second timeout; a slow one is left out of that page and flagged.
