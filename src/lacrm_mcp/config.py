"""Configuration, read only from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

API_URL = "https://api.lessannoyingcrm.com/v2/"
_TRUE = {"1", "true", "yes", "on"}


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)  # repr=False: never print the key
    read_only: bool = False
    timeout: float = 20.0
    max_retries: int = 3
    api_url: str = API_URL

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, *, force_read_only: bool = False) -> "Settings":
        env = dict(os.environ) if env is None else env
        key = env.get("LACRM_API_KEY", "").strip()
        if not key:
            raise ConfigError(
                "LACRM_API_KEY is not set. Create a key under Settings > Programmer API "
                "in Less Annoying CRM and export it as LACRM_API_KEY."
            )
        try:
            timeout = float(env.get("LACRM_TIMEOUT_SECONDS", "20"))
            retries = int(env.get("LACRM_MAX_RETRIES", "3"))
        except ValueError as exc:
            raise ConfigError("LACRM_TIMEOUT_SECONDS / LACRM_MAX_RETRIES must be numeric.") from exc
        if not (1 <= timeout <= 120):
            raise ConfigError("LACRM_TIMEOUT_SECONDS must be between 1 and 120.")
        if not (0 <= retries <= 6):
            raise ConfigError("LACRM_MAX_RETRIES must be between 0 and 6.")
        read_only = force_read_only or env.get("LACRM_READ_ONLY", "").strip().lower() in _TRUE
        return cls(api_key=key, read_only=read_only, timeout=timeout, max_retries=retries)
