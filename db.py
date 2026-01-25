import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base


def _normalize_db_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


DATABASE_URL = _normalize_db_url(os.environ.get("DATABASE_URL", "sqlite:///local.db"))

engine_kwargs = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


@contextmanager
def get_db_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
