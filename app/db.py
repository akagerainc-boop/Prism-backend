"""SQLAlchemy 2.x engine/session wiring for the XAMPP MySQL ``prism`` database.

A *synchronous* engine is used deliberately: the endpoints that touch the
database are declared as plain ``def`` (not ``async def``), so FastAPI runs them
in its threadpool and the event loop is never blocked. This keeps the DB code
simple and avoids an async MySQL driver dependency.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .config import settings
from .logging_config import get_logger

log = get_logger(__name__)

engine = create_engine(
    settings.sqlalchemy_url,
    echo=settings.sql_echo,
    pool_pre_ping=True,  # XAMPP MySQL drops idle connections aggressively
    pool_recycle=1800,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a session, committing on clean exit."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Standalone transactional scope for non-request code paths."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_connection() -> bool:
    """Return True when the ``prism`` database is reachable."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # pragma: no cover - depends on live MySQL
        log.warning("MySQL connection check failed: %s", exc)
        return False
