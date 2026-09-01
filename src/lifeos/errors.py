"""Typed LifeOS errors."""


class LifeOSError(Exception):
    """Base class for expected product errors."""


class ConfigurationError(LifeOSError):
    """Configuration is absent, malformed, or unsafe."""


class ConnectorError(LifeOSError):
    """A connector could not complete a requested operation."""


class AuthenticationRequired(ConnectorError):
    """The connector needs user authorization or reauthorization."""


class RateLimited(ConnectorError):
    """The upstream provider rejected work due to rate limits."""


class UnsafePath(LifeOSError):
    """A path escaped the configured LifeOS brain."""


class StaleProposal(LifeOSError):
    """Canon changed after a proposal was prepared."""


class ProposalNotFound(LifeOSError):
    """The requested proposal does not exist."""


class GBrainUnavailable(LifeOSError):
    """GBrain is missing or returned an unusable response."""
