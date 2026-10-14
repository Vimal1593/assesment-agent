import json
from app.schemas import AssessmentDraft


def grade_submission(draft: AssessmentDraft, submitted_answers: dict[str, str]) -> tuple[int, dict[str, str], list[dict]]:
    """Grade solely against the server-side answer key; ignore unknown IDs/choices."""
    allowed_ids = {q.id for q in draft.questions}
    clean = {qid: choice for qid, choice in submitted_answers.items() if qid in allowed_ids and choice in {"A", "B", "C", "D"}}
    feedback = []
    score = 0
    for question in draft.questions:
        answer = clean.get(question.id, "")
        correct = answer == question.correct_option_id
        score += int(correct)
        feedback.append({
            "question_id": question.id,
            "selected_option_id": answer,
            "correct_option_id": question.correct_option_id,
            "is_correct": correct,
            "explanation": question.explanation,
        })
    return score, clean, feedback


def feedback_json(feedback: list[dict]) -> str:
    return json.dumps(feedback, ensure_ascii=False)
