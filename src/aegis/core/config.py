"""Runtime configuration loaded from the environment (prefix ``AEGIS_``).

Settings grow together with the modules that consume them; no speculative
knobs for features that do not exist yet.
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AEGIS_", env_file=".env", extra="ignore")

    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
