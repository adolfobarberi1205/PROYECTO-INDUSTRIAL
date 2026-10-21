from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Configuracion de PostgreSQL cargada desde variables de entorno."""

    database_url: str | None = None
    database_echo: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def require_database_url(self) -> str:
        if not self.database_url:
            raise RuntimeError(
                "DATABASE_URL no esta configurada. "
                "La base de datos permanente aun no puede conectarse."
            )

        return self.database_url


@lru_cache
def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings()
