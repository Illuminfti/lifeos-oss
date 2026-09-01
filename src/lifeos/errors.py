"""Typed failures that callers and the MCP surface can handle."""


class LifeOSError(RuntimeError):
    pass


class ConfigurationError(LifeOSError):
    pass


class AuthenticationRequired(LifeOSError):
    pass


class AuthorizationDenied(LifeOSError):
    pass


class ProviderUnavailable(LifeOSError):
    pass


class ProviderRateLimited(LifeOSError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class FullResyncRequired(LifeOSError):
    pass


class UnsupportedCapability(LifeOSError):
    pass


class PromotionConflict(LifeOSError):
    pass


class ProposalNotFound(LifeOSError):
    pass
