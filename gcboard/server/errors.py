"""Errors returned to clients as {error, message} JSON bodies (SRD section 7)."""

from typing import Any


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str, **extra: Any) -> None:
        """`extra` fields are added to the body, e.g. the last accepted
        writeup text on a rejected save (FR-WRITE-6)."""
        super().__init__(message)
        self.status, self.error, self.message, self.extra = status, error, message, extra

    def body(self) -> dict[str, Any]:
        return {"error": self.error, "message": self.message, **self.extra}
