class BuilderError(Exception):
    """Base class for builder failures."""


class ConfigError(BuilderError):
    """Invalid configuration."""


class AssetPinMismatch(BuilderError):
    """A downloaded asset does not match its pinned SHA-256."""
