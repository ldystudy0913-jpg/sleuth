"""Mailbox user-facing errors (APPError with HTTP status)."""
from __future__ import annotations

from ..bizerror import APPError, BizErrorCode


class MailboxError(APPError):
    """User-facing mailbox validation error (maps to 400/413/503/404)."""

    def __init__(
        self,
        item: BizErrorCode,
        *args,
        status: int = 400,
        data=None,
    ):
        super().__init__(
            code=item.code,
            msg=item.format_message(*args),
            status=status,
            data=data,
        )
