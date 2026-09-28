"""Exception types. Configuration problems are RAISED (a developer must fix them);
data problems are REPORTED in the result (the caller decides what to do with bad data)."""


class ConfigError(ValueError):
    """The schema configuration is invalid or cannot be used (bad key, missing dependency, ...)."""


class SourceError(ValueError):
    """The source data cannot be parsed or would lose information (invalid JSON/XML, bad CSV, ...)."""
