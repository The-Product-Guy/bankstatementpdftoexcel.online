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
    input_storage_key = Column(String)
    output_storage_key = Column(String)
    storage_key = Column(String)
    transaction_count = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)
    input_deleted_at = Column(DateTime)
    output_deleted_at = Column(DateTime)
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


class FeedbackSubmission(Base):
    """
    Tracks user-submitted feedback when a conversion produces poor results.
    If the user opts in, their PDF is kept for analysis; otherwise only the
    metadata is stored.
    """
    __tablename__ = "feedback_submissions"

    id = Column(String, primary_key=True, default=_uuid)
    job_id = Column(String, ForeignKey("jobs.id"), index=True)
    user_id = Column(String, ForeignKey("users.id"))
    guest_id = Column(String, index=True)
    feedback_type = Column(String, nullable=False)      # 'empty_result', 'incorrect_data', 'formatting', 'other'
    message = Column(Text)                               # Optional free-text from user
    pdf_shared = Column(Boolean, default=False)          # Did user consent to share their PDF?
    pdf_storage_key = Column(String)                     # S3 key if PDF was shared
    extraction_rows = Column(Integer)                    # How many rows we extracted
    extraction_cols = Column(Integer)                    # How many columns we detected
    quality_used = Column(String)                        # 'standard' or 'high'
    status = Column(String, default="new")               # 'new', 'reviewed', 'resolved'
    created_at = Column(DateTime, default=datetime.utcnow)
    ip = Column(String)
    user_agent = Column(String)
