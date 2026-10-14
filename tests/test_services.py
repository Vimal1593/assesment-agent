import io

import pytest
from pydantic import ValidationError

from app.document_service import DocumentError, extract_text
from app.grading_service import grade_submission
from app.schemas import AssessmentDraft
from app.assessment_service import token_hash


def sample_draft():
    return AssessmentDraft.model_validate({
        "title": "Python Basics", "topic": "Python",
        "questions": [{
            "id": "q1", "question": "Which is a list?",
            "options": [
                {"id": "A", "text": "[]"}, {"id": "B", "text": "{}"},
                {"id": "C", "text": "()"}, {"id": "D", "text": "set()"}
            ], "correct_option_id": "A", "explanation": "Square brackets create a list.",
            "difficulty": "easy", "source_reference": "Lists section"
        }]
    })


def test_question_schema_rejects_duplicate_question_ids():
    data = sample_draft().model_dump()
    data["questions"].append(data["questions"][0])
    with pytest.raises(ValidationError):
        AssessmentDraft.model_validate(data)


def test_question_schema_requires_four_distinct_options():
    data = sample_draft().model_dump()
    data["questions"][0]["options"][3]["text"] = "[]"
    with pytest.raises(ValidationError):
        AssessmentDraft.model_validate(data)


def test_grading_is_deterministic_and_ignores_unknown_question_ids():
    draft = sample_draft()
    score, answers, feedback = grade_submission(draft, {"q1": "A", "fake": "A", "q2": "D"})
    assert score == 1
    assert answers == {"q1": "A"}
    assert feedback[0]["correct_option_id"] == "A"


def test_document_text_and_unsupported_extension():
    assert extract_text("notes.txt", b"  Intro to Python  ") == "Intro to Python"
    with pytest.raises(DocumentError):
        extract_text("notes.exe", b"not executable")


def test_empty_document_rejected():
    with pytest.raises(DocumentError):
        extract_text("notes.txt", b"  \n")


def test_tokens_are_hashed():
    token = "unguessable-token"
    assert token_hash(token) != token
    assert token_hash(token) == token_hash(token)


def test_parse_allowed_emails():
    from app.assessment_service import parse_allowed_emails
    raw = "alice@example.com, BOB@Example.COM;  charlie@domain.org\ndavid@test.co   alice@example.com  invalid_string "
    parsed = parse_allowed_emails(raw)
    assert parsed == ["alice@example.com", "bob@example.com", "charlie@domain.org", "david@test.co"]
    assert parse_allowed_emails("") == []
    assert parse_allowed_emails(None) == []


def test_is_email_allowed():
    import json
    from types import SimpleNamespace
    from app.assessment_service import is_email_allowed
    # Assessment with no allowed emails allows all
    open_assessment = SimpleNamespace(allowed_emails=None)
    assert is_email_allowed(open_assessment, "anyone@example.com") is True

    # Assessment with specific emails
    restricted = SimpleNamespace(allowed_emails=json.dumps(["trainee1@example.com", "trainee2@example.com"]))
    assert is_email_allowed(restricted, "trainee1@example.com") is True
    assert is_email_allowed(restricted, "TRAINEE1@EXAMPLE.COM") is True
    assert is_email_allowed(restricted, "trainee3@example.com") is False
