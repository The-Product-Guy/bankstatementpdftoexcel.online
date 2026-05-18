import pytest


def test_database_url_defaults_to_sqlite_for_local_dev(monkeypatch):
    import db

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_PROJECT_ID", raising=False)

    assert db._resolve_database_url() == "sqlite:///local.db"


def test_database_url_required_in_production(monkeypatch):
    import db

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(RuntimeError, match="DATABASE_URL must be set"):
        db._resolve_database_url()


def test_database_url_normalizes_railway_postgres_scheme(monkeypatch):
    import db

    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@host:5432/db")

    assert db._resolve_database_url() == "postgresql://user:pass@host:5432/db"
