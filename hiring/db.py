import uuid
from datetime import datetime, timezone
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def uid():
    return uuid.uuid4().hex


def now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "hiring_users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    email: Mapped[str | None] = mapped_column(String(254), unique=True)
    max_id: Mapped[str | None] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(20), default="candidate")
    company: Mapped[str] = mapped_column(String(160), default="")
    password: Mapped[str] = mapped_column(Text, default="")
    demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Job(Base):
    __tablename__ = "hiring_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey("hiring_users.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    company: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)
    terms: Mapped[str] = mapped_column(String(500), default="")
    requirements: Mapped[list] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Application(Base):
    __tablename__ = "hiring_applications"
    __table_args__ = (UniqueConstraint("job_id", "user_id", name="uq_hiring_application"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(ForeignKey("hiring_jobs.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("hiring_users.id"), index=True)
    resume: Mapped[str] = mapped_column(Text)
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    questions: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="clarifying")
    invitation: Mapped[str] = mapped_column(Text, default="")
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Audit(Base):
    __tablename__ = "hiring_audit"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    application_id: Mapped[str] = mapped_column(ForeignKey("hiring_applications.id"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BotSession(Base):
    __tablename__ = "hiring_bot_sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("hiring_users.id"), primary_key=True)
    state: Mapped[dict] = mapped_column(JSON, default=dict)


class BotEvent(Base):
    __tablename__ = "hiring_bot_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Outbox(Base):
    __tablename__ = "hiring_outbox"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    max_id: Mapped[str] = mapped_column(String(40))
    body: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def connect(url):
    kwargs = {"connect_args": {"check_same_thread": False, "timeout": 20}} if url.startswith("sqlite") else {}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def pragma(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)
