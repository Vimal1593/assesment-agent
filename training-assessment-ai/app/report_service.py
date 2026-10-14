import csv
import io
from datetime import datetime


def _spreadsheet_safe(value):
    """Prefix formula-leading text to reduce CSV formula injection in spreadsheet apps."""
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def submissions_csv(assessment, submissions) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["assessment_id", "assessment_title", "trainee_name", "trainee_identifier", "score", "total_questions", "percentage", "submitted_at"])
    for submission in submissions:
        pct = round(submission.score * 100 / submission.total_questions, 2) if submission.total_questions else 0
        writer.writerow([assessment.id, _spreadsheet_safe(assessment.title), _spreadsheet_safe(submission.trainee_name),
                         _spreadsheet_safe(submission.trainee_identifier), submission.score, submission.total_questions,
                         pct, submission.submitted_at.isoformat()])
    return output.getvalue()
