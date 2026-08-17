"""Connection factory for the optional Valkey-backed coordination layer.

See the `valkey_url` comment block in anum_api/settings.py for the intended
semantics: unset keeps every caller on today's in-memory stores, set to a
`redis://...` URL switches them to this client. Valkey speaks the Redis
wire protocol, so redis-py works against it unmodified - nothing here is
Valkey-specific beyond the name.
"""

from __future__ import annotations

import redis


def build_redis_client(url: str | None) -> redis.Redis | None:
    """Return a connected `redis.Redis` for `url`, or None when unset.

    `decode_responses=True` so callers get `str` back instead of `bytes`,
    matching how the rest of this codebase handles text.
    """

    if not url:
        return None
    return redis.Redis.from_url(url, decode_responses=True)
