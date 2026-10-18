"""Brevo email campaigns for the workspace (docs/brevo.md).

A per-workspace Brevo connection (the owner's API key, Fernet-encrypted), read access to
contact lists, verified senders, campaigns and their reports, campaign drafts written in
ANUM, a test send to the Brevo account's own address, and sending or scheduling that
always waits for an owner's approval bound to the exact draft. The same reads, the draft
and the send are agent tools under the runtime's tool policy.
"""

from .api import router
from .tools import register_brevo_tools

__all__ = ["register_brevo_tools", "router"]
