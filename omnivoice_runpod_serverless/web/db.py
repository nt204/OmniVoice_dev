from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import time
from sqlalchemy import create_engine, text
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/omnivoice_web")
Base = declarative_base()


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite:")


def _create_engine(database_url: str) -> Engine:
    kwargs = {"future": True}
    if _is_sqlite(database_url):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(
            {
                "pool_size": 20,
                "max_overflow": 10,
                "pool_recycle": 3600,
                "pool_pre_ping": True,
            }
        )
    return create_engine(database_url, **kwargs)


engine = _create_engine(DATABASE_URL)

if not _is_sqlite(DATABASE_URL):
    @event.listens_for(engine, "connect")
    def set_postgres_timezone(dbapi_connection, connection_record):  # type: ignore[no-redef]
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SET TIME ZONE 'Asia/Ho_Chi_Minh'")
        finally:
            cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def wait_for_database(max_attempts: int = 20, delay_seconds: float = 3.0) -> None:
    for attempt in range(1, max_attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            print("Database connection successful!")
            return
        except Exception as exc:
            if attempt == max_attempts:
                raise RuntimeError("Could not connect to database after several attempts") from exc
            print(f"Waiting for database... ({attempt}/{max_attempts}) - {exc}")
            time.sleep(delay_seconds)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
