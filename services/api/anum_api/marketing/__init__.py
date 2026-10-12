"""Social media marketing for the owner's apps and tyre business (docs/marketing.md).

Connected accounts through each platform's official API (OAuth, tokens encrypted at
rest), a planner (campaigns, calendar, per-platform drafts in English and Arabic, media
from Files, UTM links), AI assist through the budgeted model gateway, governed
publishing (an approval before anything is posted, or an owner-approved campaign
auto-post), analytics pulled from the platforms, and tools for chat and voice.
"""

from .api import router
from .publisher import run_due_posts
from .tools import register_marketing_tools

__all__ = ["register_marketing_tools", "router", "run_due_posts"]
