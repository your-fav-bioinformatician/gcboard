"""Errors returned to clients as {error, message} JSON bodies (SRD section 7)."""


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str) -> None:
        super().__init__(message)
        self.status, self.error, self.message = status, error, message
