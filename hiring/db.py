import uuid
from datetime import datetime, timezone
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event, inspect, text
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
    screening_questions: Mapped[list] = mapped_column(JSON, default=list)
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
    application_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    callback_id: Mapped[str | None] = mapped_column(String(256), nullable=True)


class BotAction(Base):
    __tablename__ = 'hiring_bot_actions'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('hiring_users.id'), index=True)
    command: Mapped[str] = mapped_column(String(200))
    expected_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class BotAttempt(Base):
    __tablename__ = 'hiring_bot_attempts'
    user_id: Mapped[str] = mapped_column(ForeignKey('hiring_users.id'), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class SandboxSwitch(Base):
    __tablename__ = 'hiring_sandbox_switches'
    max_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    persona: Mapped[str] = mapped_column(String(1), default='e')


class IntegrationKey(Base):
    __tablename__ = 'hiring_integration_keys'
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey('hiring_users.id'), index=True)
    name: Mapped[str] = mapped_column(String(80))
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class ExternalJob(Base):
    __tablename__ = 'hiring_external_jobs'
    __table_args__ = (UniqueConstraint('owner_id', 'source', 'external_id'),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey('hiring_users.id'), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('hiring_jobs.id'), unique=True)
    source: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(120))


class IntegrationEvent(Base):
    __tablename__ = 'hiring_integration_events'
    __table_args__ = {'sqlite_autoincrement': True}
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('hiring_users.id'), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def connect(url):
    kwargs = {"connect_args": {"check_same_thread": False, "timeout": 20}} if url.startswith("sqlite") else {}
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def pragma(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    # Additive v1 -> v2 migration. Startup is single-process; existing data is kept.
    columns = {c['name'] for c in inspect(engine).get_columns('hiring_outbox')}
    with engine.begin() as connection:
        for name, size in (('application_id', 32), ('callback_id', 256)):
            if name not in columns:
                connection.execute(text(f'ALTER TABLE hiring_outbox ADD COLUMN {name} VARCHAR({size})'))
        if 'screening_questions' not in {c['name'] for c in inspect(engine).get_columns('hiring_jobs')}:
            connection.execute(text("ALTER TABLE hiring_jobs ADD COLUMN screening_questions JSON NOT NULL DEFAULT '[]'"))
    from .integration_events import install_events
    install_events()
    return engine, sessionmaker(engine, expire_on_commit=False)


def serialize_writes(db):
    """Serialize event/withdraw/send on SQLite, including cross-process startup mistakes."""
    if db.bind.dialect.name == 'sqlite':
        db.execute(text('BEGIN IMMEDIATE'))
