"""Environment variable parsing shared by the service and the MCP server.

Empty values count as unset, so `SERVICE_PORT=` in a .env file falls back to
the default instead of failing.
"""

import os


class ConfigError(ValueError):
    """A setting has a value the service cannot use."""


def env_str(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, "").strip()
    return value or default


def env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = env_str(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from None
    return _check_range(name, value, minimum, maximum)


def env_float(
    name: str, default: float, *, minimum: float | None = None, maximum: float | None = None
) -> float:
    raw = env_str(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
    return _check_range(name, value, minimum, maximum)


def _check_range(name, value, minimum, maximum):
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name} must be at most {maximum}, got {value}")
    return value
