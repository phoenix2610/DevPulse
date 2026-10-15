import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, SmallInteger, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .config import get_settings
from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def new_id() -> str:
    return uuid.uuid4().hex


TS = DateTime(timezone=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    tier: Mapped[str] = mapped_column(String(20), default="free")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    repositories: Mapped[list["Repository"]] = relationship(back_populates="user")


class GitHubConnection(Base):
    __tablename__ = "github_connections"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    github_login: Mapped[str] = mapped_column(String(100))
    access_token_enc: Mapped[str] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class Repository(Base):
    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("user_id", "url"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    url: Mapped[str] = mapped_column(String(500))
    name: Mapped[str] = mapped_column(String(255))
    private: Mapped[bool] = mapped_column(Boolean, default=False)
    installation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    user: Mapped[User] = relationship(back_populates="repositories")
    jobs: Mapped[list["AnalysisJob"]] = relationship(back_populates="repository")


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    __table_args__ = (Index("ix_job_user_status", "user_id", "status"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    queue_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_purged_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    repository: Mapped[Repository] = relationship(back_populates="jobs")
    issues: Mapped[list["CodeIssue"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    logs: Mapped[list["JobLog"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobLog(Base):
    __tablename__ = "job_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.id"), index=True)
    level: Mapped[str] = mapped_column(String(10), default="info")
    agent: Mapped[str] = mapped_column(String(40), default="system")
    message: Mapped[str] = mapped_column(Text)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    job: Mapped[AnalysisJob] = relationship(back_populates="logs")


class CodeIssue(Base):
    __tablename__ = "code_issues"
    __table_args__ = (Index("ix_issue_job_file", "job_id", "file_path"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.id"))
    file_path: Mapped[str] = mapped_column(String(1000))
    line_start: Mapped[int] = mapped_column(Integer)
    line_end: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(20))
    agent: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(String(40), default="static")
    prompt_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    job: Mapped[AnalysisJob] = relationship(back_populates="issues")


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (UniqueConstraint("repository_id", "fingerprint"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(20))
    file_path: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(12), default="open")
    first_seen_at: Mapped[datetime] = mapped_column(TS)
    last_seen_at: Mapped[datetime] = mapped_column(TS)
    last_seen_job_id: Mapped[str] = mapped_column(String(32))
    resolved_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class IssueFeedback(Base):
    __tablename__ = "issue_feedback"
    __table_args__ = (UniqueConstraint("issue_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("code_issues.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    vote: Mapped[int] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(40))
    prompt_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class LLMCall(Base):
    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_jobs.id"), index=True, nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True, nullable=True)
    agent: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    redactions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow, index=True)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("name", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[str] = mapped_column(String(40))
    system: Mapped[str] = mapped_column(Text)
    user_template: Mapped[str] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class DocChunk(Base):
    __tablename__ = "doc_chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(300), unique=True)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(JSON().with_variant(Vector(get_settings().embedding_dim), "postgresql"))
