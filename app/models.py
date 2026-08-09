from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db import GUID, Base, new_uuid, utcnow


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[str] = mapped_column(GUID(), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[str] = mapped_column(GUID(), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    username_normalized: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    total_xp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    roles: Mapped[list[Role]] = relationship(secondary="user_roles")


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(96), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    user_agent: Mapped[str] = mapped_column(Text, default="", nullable=False)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    user: Mapped[User] = relationship()


class Problem(Base):
    __tablename__ = "problems"
    __table_args__ = (
        CheckConstraint("level >= 1 and level <= 30", name="ck_problem_level"),
        CheckConstraint("base_xp >= 0", name="ck_problem_base_xp"),
        Index("ix_problems_filters", "grade", "semester", "unit", "level", "state"),
    )
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    external_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    grade: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    semester: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    unit: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    tags: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), default=dict, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    base_xp: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="draft", nullable=False, index=True)
    current_version_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    versions: Mapped[list[ProblemVersion]] = relationship(
        back_populates="problem", cascade="all, delete-orphan"
    )


class ProblemVersion(Base):
    __tablename__ = "problem_versions"
    __table_args__ = (
        UniqueConstraint("problem_id", "version_number", name="uq_problem_version_number"),
        UniqueConstraint("problem_id", "content_hash", name="uq_problem_version_content"),
        Index("ix_problem_versions_problem_created", "problem_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    problem_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("problems.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    answer_type: Mapped[str] = mapped_column(String(24), nullable=False)
    choices: Mapped[list] = mapped_column(MutableList.as_mutable(JSON), default=list, nullable=False)
    answer_spec: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), default=dict, nullable=False)
    explanation_html: Mapped[str] = mapped_column(Text, default="", nullable=False)
    diagram_svg: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    problem: Mapped[Problem] = relationship(back_populates="versions")


class Exam(Base):
    __tablename__ = "exams"
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    time_limit_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(GUID(), nullable=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ExamVersion(Base):
    __tablename__ = "exam_versions"
    __table_args__ = (
        UniqueConstraint("exam_id", "version_number", name="uq_exam_version_number"),
        UniqueConstraint("exam_id", "content_hash", name="uq_exam_version_content"),
    )
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    exam_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    time_limit_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    items: Mapped[list[ExamVersionItem]] = relationship(
        back_populates="exam_version", cascade="all, delete-orphan"
    )


class ExamVersionItem(Base):
    __tablename__ = "exam_version_items"
    __table_args__ = (UniqueConstraint("exam_version_id", "sequence", name="uq_exam_item_sequence"),)
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    exam_version_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("exam_versions.id", ondelete="CASCADE"), nullable=False
    )
    problem_version_id: Mapped[str] = mapped_column(GUID(), ForeignKey("problem_versions.id"), nullable=False)
    problem_id: Mapped[str] = mapped_column(GUID(), ForeignKey("problems.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    exam_version: Mapped[ExamVersion] = relationship(back_populates="items")
    problem_version: Mapped[ProblemVersion] = relationship()
    problem: Mapped[Problem] = relationship()


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (Index("ix_attempts_user_status", "user_id", "status", "created_at"),)
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    exam_version_id: Mapped[str] = mapped_column(GUID(), ForeignKey("exam_versions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="in_progress", nullable=False)
    snapshot: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), default=dict, nullable=False)
    answers: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), default=dict, nullable=False)
    flags: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), default=dict, nullable=False)
    submitted_answers: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict, nullable=False
    )
    result_snapshot: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), default=dict, nullable=False)
    submission_idempotency_key: Mapped[str | None] = mapped_column(String(240))
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    xp_awarded: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    deadline_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    submitted_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))


class AttemptAnswer(Base):
    __tablename__ = "attempt_answers"
    __table_args__ = (UniqueConstraint("attempt_id", "sequence", name="uq_attempt_answer_sequence"),)
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    attempt_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("attempts.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    answer: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), default=dict, nullable=False)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ProblemSolve(Base):
    __tablename__ = "problem_solves"
    __table_args__ = (UniqueConstraint("user_id", "problem_id", name="uq_problem_solve_once"),)
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    problem_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("problems.id", ondelete="CASCADE"), nullable=False, index=True
    )
    first_problem_version_id: Mapped[str] = mapped_column(
        GUID(), ForeignKey("problem_versions.id"), nullable=False
    )
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class XpLedger(Base):
    __tablename__ = "xp_ledger"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_xp_ledger_idempotency"),
        Index("ix_xp_ledger_user_created", "user_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    problem_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("problems.id"))
    attempt_id: Mapped[str | None] = mapped_column(GUID(), ForeignKey("attempts.id"))
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
