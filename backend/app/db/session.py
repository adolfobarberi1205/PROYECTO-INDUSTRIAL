from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.config import get_database_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_database_settings()

    return create_engine(
        settings.require_database_url(),
        echo=settings.database_echo,
        pool_pre_ping=True,
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_engine(),
        autoflush=False,
        expire_on_commit=False,
    )


def get_database_session() -> Generator[Session, None, None]:
    """Dependencia de FastAPI que abre una sesion por solicitud."""

    session = get_session_factory()()

    try:
        yield session
    finally:
        session.close()
