"""Server-sent event framing shared by the streamed assistant replies."""

from __future__ import annotations

import json

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def sse(event: str, data: object) -> str:
    """One event; the JSON payload keeps newlines in the text from breaking the frame."""
    return f"event: {event}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n"
