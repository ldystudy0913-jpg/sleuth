"""Mailbox user-facing errors."""
from __future__ import annotations


class MailboxError(ValueError):
    """User-facing mailbox validation error (maps to 400/413/503)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = int(status)
