class DomainError(Exception):
    status_code: int = 400
    error_code: str = "domain_error"

    def __init__(self, message: str, *, error_code: str | None = None, **context: object) -> None:
        super().__init__(message)
        self.message = message
        if error_code is not None:
            self.error_code = error_code
        self.context = context


class NotFoundError(DomainError):
    status_code = 404
    error_code = "not_found"


class PermissionDeniedError(DomainError):
    status_code = 403
    error_code = "permission_denied"


class IntegrationError(DomainError):
    status_code = 502
    error_code = "integration_error"


class RateLimitedError(DomainError):
    status_code = 429
    error_code = "rate_limited"


class ConflictError(DomainError):
    status_code = 409
    error_code = "conflict"
