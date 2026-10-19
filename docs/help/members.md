---
id: members
title: Members
summary: Invite people with a single-use link, change their roles, deactivate them, and accept an invitation.
keywords: members, invitation, invite, role, owner, member, viewer, deactivate, reactivate, revoke, accept
docs: ../identity.md
---

# Members

Members shows who can work in this workspace and with which role. Owners invite people with a single-use link and manage roles; anyone can accept an invitation issued to them here.

## Invite someone

1. Open **Members** in the sidebar under **Admin**.
2. Under **Invite someone**, fill **User id**, **Email** or both, so only that person can accept it.
3. Pick the **Role** and set **Expires after (hours)**, then click **Create invitation**.
4. Copy the token or link with **Copy token** or **Copy link** and send it to the person. It is shown only once; click **Done** when finished.
5. Under **Invitations**, filter with **Pending**, **Accepted**, **Revoked**, **Expired** or **All**. Use **Resend** (or **Renew** for an expired one) or **Revoke**.

## Change a role or deactivate someone

1. In the member list, search by user id or email, or filter with **Status** and **Role**.
2. Pick a new role in the member's role menu and confirm with **Change role**.
3. Click **Deactivate** and confirm to remove someone's access, or **Reactivate** to restore it.
4. The last active owner cannot be changed or deactivated: make someone else an owner first.

## Accept an invitation

1. Open **Members** while signed in as the person the invitation was issued to.
2. Under **Accept an invitation**, paste the token or link into **Invitation token or link**. **Workspace** fills in from a link.
3. Click **Accept invitation**. If you joined another workspace, click **Switch to** that workspace.

## Who can do this

- Workspace owners: list members, invite, resend and revoke invitations, change roles, and deactivate or reactivate members. Others see that the owner role is required.
- Anyone signed in: accept an invitation bound to their own user id or email.

## Learn more

[Identity and Sign-In](../identity.md)
