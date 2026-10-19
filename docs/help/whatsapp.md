---
id: whatsapp
title: WhatsApp
summary: Answer customers on your WhatsApp Business number with AI drafts and approved sends.
keywords: whatsapp, customer, chat, message, template, reply, approval, meta, business number
docs: ../whatsapp.md
---

# WhatsApp

WhatsApp brings customer chats from your WhatsApp Business number (Meta's WhatsApp Cloud API) into ANUM. Messages are sorted on arrival, ANUM can suggest replies, and sending waits for approval.

## Connect your WhatsApp Business number

1. Open **WhatsApp** in the sidebar under **More** and choose the **Setup** tab.
2. Fill **Phone number ID**, **WhatsApp Business Account ID**, **Permanent access token (system user)**, **App secret** and **Webhook verify token** (use **Generate** for a new one).
3. Click **Connect**.
4. Copy the **Callback URL for Meta** into your Meta app's webhook with the same verify token, then click **Check connection** or **Send a test to my number**.

## Reply to a customer

1. On the **Inbox** tab, search with **Search conversations** or pick **All**, **Unread**, **Needs a reply** or **Awaiting approval**.
2. Open a conversation. Click **Suggest a reply**, check it, then **Use this draft**, or type in **Your reply**.
3. Click **Send for approval**.
4. An owner checks the payload hash and clicks **Approve & send** or **Reject**, in the conversation or in Approvals.

## Send a template after 24 hours

1. If the customer last wrote more than 24 hours ago, WhatsApp allows only approved templates.
2. Under **Reply with**, choose **Template**, then **Choose an approved template**.
3. Fill every **Value** and check the preview, then click **Send for approval** (or **Send now** for a template on the auto-send list).
4. On the **Templates** tab, owners click **Refresh from Meta** and can turn on **Send without asking (low-risk replies such as order status)** for a template.

## Who can do this

- Owners: everything, including connecting the number, the auto-send list and approving sends.
- Members: read conversations and templates, draft replies and ask to send.
- Viewers: cannot see customer conversations.

## Learn more

[WhatsApp Business inbox](../whatsapp.md)
