import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import Pool

DEFAULT_DATABASE_URL = "sqlite:///./local.db"


def get_database_url() -> str:
    turso_database_url = os.getenv("TURSO_DATABASE_URL")
    turso_auth_token = os.getenv("TURSO_AUTH_TOKEN")

    if turso_database_url and turso_auth_token:
        return f"sqlite+{turso_database_url}?secure=true"

    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_connect_args() -> dict[str, bool | str]:
    database_url = get_database_url()

    if database_url.startswith("sqlite+libsql"):
        return {"auth_token": os.environ["TURSO_AUTH_TOKEN"]}

    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}

    return {}


def create_database_engine(poolclass: type[Pool] | None = None) -> Engine:
    engine_options = {"connect_args": get_connect_args()}
    if poolclass is not None:
        engine_options["poolclass"] = poolclass

    return create_engine(get_database_url(), **engine_options)


engine = create_database_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
