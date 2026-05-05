"""SQLAlchemy models and session factory."""

import os
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/jobs.db")

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("job_id", name="uq_job_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    company: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    location: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    remote_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    posted_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    description_raw: Mapped[str] = mapped_column(Text, nullable=False, default="")
    salary_range: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    # Stage 1 filter
    passed_prefilter: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Stage 2 LLM scoring
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    score_flags: Mapped[str | None] = mapped_column(Text, nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<Job {self.company} — {self.title}>"


def get_session() -> Session:
    return SessionLocal()


def init_db() -> None:
    os.makedirs("data", exist_ok=True)
    Base.metadata.create_all(engine)
