"""Email Center: the workspace's own mail server over IMAP and SMTP (docs/email.md#email-center).

The person types only their mailbox address and its password (a mail server app
password); the server host and ports come from ``ANUM_MAILSERVER_*`` operator settings.
The browser never talks to the mail server: every IMAP and SMTP call runs here, on the API.
"""
