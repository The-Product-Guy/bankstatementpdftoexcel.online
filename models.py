import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    ForeignKey,
)
from sqlalchemy.orm import declarative_base


Base = declarative_base()


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=_uuid)
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String)
    role = Column(String, default="user")
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime)
    is_active = Column(Boolean, default=True)
    plan_id = Column(String, default="free")
    plan_status = Column(String, default="free")
    trial_ends_at = Column(DateTime)
    stripe_customer_id = Column(String)
    stripe_subscription_id = Column(String)


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    token_hash = Column(String, unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    ip = Column(String)
    user_agent = Column(String)


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id"))
    guest_id = Column(String, index=True)
    filename = Column(String)
    file_size_bytes = Column(Integer)
    page_count = Column(Integer)
    status = Column(String, default="queued")
    error = Column(Text)
    storage_key = Column(String)
    transaction_count = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    ip = Column(String)
    user_agent = Column(String)


class UsageCounter(Base):
    __tablename__ = "usage_counters"
    __table_args__ = (
        UniqueConstraint("user_id", "guest_id", "scope", name="uq_usage_scope"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id"))
    guest_id = Column(String, index=True)
    scope = Column(String, default="lifetime")
    conversions_count = Column(Integer, default=0)
    pages_total = Column(Integer, default=0)
    bytes_total = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
