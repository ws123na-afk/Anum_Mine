# Sales demo workspace

A workspace called **Demo**, in the owner's own organization, filled with clearly labelled sample data so ANUM can be shown to a customer without touching real records. Nothing can be sent from it.

## Who can use it

- Only a workspace **owner** can create or reset it (`GET /api/v1/demo` and `POST /api/v1/demo/reset` answer `403` to members and viewers, with a message that names the owner role).
- The owner who creates or resets it becomes an owner of the Demo workspace. Other people join it through the normal invitation flow.

## Web

Settings, Workspace tab, card **Sales demo workspace**:

- **Create demo workspace** when there is none yet; **Reset demo data** (with a confirmation) when it exists; **Open demo workspace** switches to it.
- The card shows how many sample records the workspace holds and when it was last filled.
- While the Demo workspace is open, every page shows the banner "Demo workspace: sample data only. Nothing is sent from here."
- States: loading, owner-only (for members and viewers), error with a retry, and ready.

## API

- `GET /api/v1/demo` returns `{workspace_id, name, exists, records, counts, filled_at, sends_blocked}`.
- `POST /api/v1/demo/reset` creates the Demo workspace when it is missing, removes the sample records it holds and fills it again. It returns the same shape and writes a `demo.reset` audit record in the Demo workspace.
- The Demo workspace id is derived from the tenant id (`ws_demo_<hash>`): the same on every call and unique to the tenant. The `ws_demo_` prefix is reserved, so `POST /api/v1/organization/workspaces` refuses it with `422`.

## Sample data

Every record title starts with `Demo: ` and uses `example.com` addresses:

- **Tasks**: four tasks with priorities and due dates.
- **Approvals**: two pending approvals (an email send and a marketing publish) proposed by a sample teammate, so an owner can decide them under the two-person rule.
- **Email**: a never-connected sample mailbox (it needs sign-in, so background sync skips it) with three local threads.
- **Calendar**: a call, a product demo and a review in the coming days. No invitations are sent.
- **Business**: three accounts with contacts, leads and opportunities, and one draft proposal with a priced version.
- **Marketing**: one draft campaign with an English and an Arabic draft post on a sample, never-connected company page.
- **Files and knowledge**: a brief, a pricing sheet and an objection-handling note, collected in a **Demo: Sales playbook** knowledge collection.

## The demo tag and reset

- Every sample record is tagged in `demo_records` (migration `0075_demo_records`, forced RLS on `anum.tenant_id` and `anum.workspace_id`): the module and the record's id.
- Reset removes exactly the tagged records, inside the Demo workspace's tenant context, through each module's own service and store functions (`business.store`, `calendar_api`, `email_inbox`, `files`, `knowledge_base`, `marketing.store`). Records somebody added to the Demo workspace by hand stay.
- Tasks and approvals have no delete in ANUM: reset archives demo tasks and expires their pending approvals, so they leave the active lists.
- Social accounts have no delete: the sample company page is created once and reused.
- Seeding never uses raw SQL; it calls the same public functions the modules' own routes use.

## Sending

Nothing leaves a Demo workspace (`anum_api/demo.py`, `send_refusal`):

- The tool registry (`ToolRegistry.execute`) refuses, before the handler runs, every tool with a delivery channel or an integration target, every outward action kind (send, reply, publish, payment, price or stock change, merge, or an unknown effect) and every call whose arguments name recipients. Reads, drafts and internal changes still run.
- Routes that send without a tool refuse too: Email Center delivery (`mailbox.service.deliver`, which also covers scheduled sends), auto-forwarding, Brevo test sends, WhatsApp test messages, browser phone calls (the call token and the provider webhook) and marketing publishing.
- Covered by `services/api/tests/test_demo_workspace.py`, together with seeding, reset and per-tenant isolation.

## Related

- [In-app help](help.md), which has a [Demo workspace article](help/demo.md).
- [Identity and sign-in](identity.md) for workspaces and memberships.
