import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from app.config import get_settings
from app.models import Assessment, utcnow
from app.schemas import AssessmentDraft


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_assessment(db, *, title: str, topic: str, instructions: str, difficulty: str, question_count: int,
                      time_limit_minutes: int | None, trainee_group: str | None, source_text: str, draft: AssessmentDraft) -> Assessment:
    item = Assessment(
        title=title.strip() or draft.title, topic=topic.strip() or draft.topic,
        instructions=instructions.strip() or "Answer every question.", difficulty=difficulty,
        question_count=len(draft.questions), time_limit_minutes=time_limit_minutes,
        trainee_group=(trainee_group or "").strip() or None, source_text=source_text,
        questions_json=draft.model_dump_json(), status="draft", show_explanations=1,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def publish_assessment(db, assessment: Assessment) -> str:
    if assessment.status == "published" and assessment.access_token_hash:
        # Do not expose or reconstruct the raw token from its hash. A new link is generated on republish.
        token = secrets.token_urlsafe(32)
        assessment.access_token_hash = token_hash(token)
    else:
        token = secrets.token_urlsafe(32)
        assessment.access_token_hash = token_hash(token)
    assessment.status = "published"
    assessment.published_at = utcnow()
    assessment.token_expires_at = utcnow() + timedelta(hours=get_settings().assessment_token_ttl_hours)
    db.commit()
    return token


def load_draft(assessment: Assessment) -> AssessmentDraft:
    return AssessmentDraft.model_validate_json(assessment.questions_json)
