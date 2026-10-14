from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    topic: Mapped[str] = mapped_column(String(300), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, default="Answer every question.", nullable=False)
    difficulty: Mapped[str] = mapped_column(String(20), default="mixed", nullable=False)
    question_count: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    time_limit_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trainee_group: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    questions_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    access_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    show_explanations: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submissions: Mapped[list["Submission"]] = relationship(back_populates="assessment", cascade="all, delete-orphan")


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (UniqueConstraint("assessment_id", "trainee_identifier", name="uq_submission_trainee"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False)
    trainee_name: Mapped[str] = mapped_column(String(200), nullable=False)
    trainee_identifier: Mapped[str] = mapped_column(String(320), nullable=False)
    answers_json: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    total_questions: Mapped[int] = mapped_column(Integer, nullable=False)
    feedback_json: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    assessment: Mapped[Assessment] = relationship(back_populates="submissions")
