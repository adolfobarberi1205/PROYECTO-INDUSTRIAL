from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class DatabaseSettings(BaseSettings):
    """Configuracion de PostgreSQL cargada desde variables de entorno."""

    database_url: str | None = None
    database_echo: bool = False
    database_host_override: str | None = None

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

        if self.database_host_override:
            # Docker Desktop reaches Windows through this optional host.
            # Keep credentials, database, port and query options unchanged.
            return make_url(self.database_url).set(
                host=self.database_host_override,
            ).render_as_string(hide_password=False)

        return self.database_url


@lru_cache
def get_database_settings() -> DatabaseSettings:
    return DatabaseSettings()
