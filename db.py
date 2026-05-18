import os
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from models import Base


def _normalize_db_url(url: str) -> str:
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def _is_production_runtime() -> bool:
    flask_env = os.environ.get("FLASK_ENV", "").strip().lower()
    app_env = os.environ.get("APP_ENV", "").strip().lower()
    return (
        flask_env == "production"
        or app_env == "production"
        or bool(os.environ.get("RAILWAY_ENVIRONMENT"))
        or bool(os.environ.get("RAILWAY_PROJECT_ID"))
    )


def _resolve_database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return _normalize_db_url(database_url)
    if _is_production_runtime():
        raise RuntimeError(
            "DATABASE_URL must be set in production. Connect the Railway Postgres "
            "service to both the web and worker services."
        )
    return "sqlite:///local.db"


DATABASE_URL = _resolve_database_url()

engine_kwargs = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_job_storage_columns()


def _ensure_job_storage_columns() -> None:
    """
    Keep existing databases compatible without introducing a migration framework.
    New installs get these columns from SQLAlchemy metadata; older DBs need them
    added after create_all().
    """
    inspector = inspect(engine)
    if not inspector.has_table("jobs"):
        return

    existing = {column["name"] for column in inspector.get_columns("jobs")}
    storage_columns = {
        "input_storage_key": "VARCHAR",
        "output_storage_key": "VARCHAR",
        "input_deleted_at": "TIMESTAMP",
        "output_deleted_at": "TIMESTAMP",
    }
    missing = [
        (name, column_type)
        for name, column_type in storage_columns.items()
        if name not in existing
    ]
    if not missing:
        return

    with engine.begin() as conn:
        for name, column_type in missing:
            conn.execute(text(f"ALTER TABLE jobs ADD COLUMN {name} {column_type}"))


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
