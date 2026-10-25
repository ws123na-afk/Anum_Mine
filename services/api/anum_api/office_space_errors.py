"""The error the office stores raise; the API maps ``code`` to an HTTP status."""
from __future__ import annotations


class SpaceStoreError(Exception):
    """A refused office change.

    ``code`` picks the HTTP status (invalid 422, not_found 404, conflict and restricted
    409). ``reason`` is a stable machine code and ``issues`` lists each problem as
    (location, machine code, message), so a client can mark the exact rooms involved.
    """

    def __init__(self, code: str, message: str, *, reason: str | None = None,
                 issues: tuple[tuple[str, str, str], ...] = ()):
        super().__init__(message)
        self.code = code
        self.reason = reason
        self.issues = tuple(issues)
