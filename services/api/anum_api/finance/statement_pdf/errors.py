"""The one error the statement reader raises: a stable ``code`` for the client and a message that says how to fix it."""

from __future__ import annotations


class PdfStatementError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


__all__ = ["PdfStatementError"]
