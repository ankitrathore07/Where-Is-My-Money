from typing import Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

# Lazy-initialized engine and session factory so the application can control
# lifecycle (init on startup, dispose on shutdown).
engine = None
SessionLocal = None


def init_engine(database_url: Optional[str] = None):
    """Initialize the SQLAlchemy engine and session factory.

    Returns the created engine.
    """
    global engine, SessionLocal
    url = database_url or settings.database_url
    engine = create_engine(url, echo=False, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return engine


def get_engine():
    return engine


def get_db() -> Generator:
    """Yield a database session for FastAPI dependencies or manual use.

    If the session factory hasn't been initialized, initialize it from settings.
    """
    global SessionLocal
    if SessionLocal is None:
        init_engine()

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def dispose_engine() -> None:
    """Dispose the engine and free connections."""
    global engine
    if engine is not None:
        engine.dispose()
