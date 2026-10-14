import json
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from app.assessment_service import create_assessment, is_email_allowed, load_draft, parse_allowed_emails, publish_assessment, token_hash
from app.config import get_settings
from app.database import get_db, init_db
from app.document_service import DocumentError, extract_text
from app.grading_service import grade_submission, feedback_json
from app.models import Assessment, Submission
from app.question_generator import GenerationError, generate_assessment
from app.report_service import submissions_csv
from app.schemas import AssessmentDraft

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)
settings = get_settings()
app = FastAPI(title="Training Assessment AI Agent", version="1.0.0")
app.add_middleware(SessionMiddleware, secret_key=settings.app_secret_key or secrets.token_urlsafe(48), same_site="lax", https_only=settings.session_https_only, max_age=60 * 60 * 8)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.on_event("startup")
def startup() -> None:
    if not settings.app_secret_key:
        if settings.app_env.lower() == "production":
            raise RuntimeError("APP_SECRET_KEY is required in production. Set a long random value in .env.")
        logger.warning("APP_SECRET_KEY is unset; sessions will not survive application restarts")
    if not settings.app_admin_password:
        logger.warning("APP_ADMIN_PASSWORD is unset; trainer login is disabled until configured")
    init_db()


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


templates.env.globals["csrf_token"] = csrf_token
templates.env.globals["config_max_upload"] = settings.max_upload_mb


async def validate_csrf(request: Request) -> None:
    form = await request.form()
    supplied = str(form.get("csrf_token", ""))
    expected = str(request.session.get("csrf_token", ""))
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="The form expired or failed security validation. Reload the page and try again.")


def require_trainer(request: Request) -> None:
    if request.session.get("trainer_authenticated") is not True:
        raise HTTPException(status_code=303, headers={"Location": "/login"})


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    return templates.TemplateResponse(request=request, name=name, context={"request": request, **(context or {})}, status_code=status_code)


def trainer_redirect() -> RedirectResponse:
    return RedirectResponse("/", status_code=303)


def get_assessment_or_404(db: Session, assessment_id: int) -> Assessment:
    item = db.get(Assessment, assessment_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return item


def trainer_guard(request: Request):
    if request.session.get("trainer_authenticated") is not True:
        return RedirectResponse("/login", status_code=303)
    return None


def safe_questions_for_trainee(draft: AssessmentDraft) -> list[dict]:
    return [{"id": q.id, "question": q.question, "options": [o.model_dump() for o in q.options], "difficulty": q.difficulty} for q in draft.questions]


@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get("trainer_authenticated") is True:
        return trainer_redirect()
    return render(request, "login.html", {"error": None})


@app.post("/login", response_class=HTMLResponse, dependencies=[Depends(validate_csrf)])
async def login(request: Request, password: str = Form(...)):
    if not settings.app_admin_password:
        return render(request, "login.html", {"error": "Trainer login is not configured. Set APP_ADMIN_PASSWORD in .env and restart the app."}, 503)
    if not secrets.compare_digest(password, settings.app_admin_password):
        logger.warning("Rejected trainer login attempt")
        return render(request, "login.html", {"error": "Incorrect password."}, 401)
    request.session.clear()
    request.session["trainer_authenticated"] = True
    request.session["csrf_token"] = secrets.token_urlsafe(32)
    return trainer_redirect()


@app.post("/logout", dependencies=[Depends(validate_csrf)])
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    guard = trainer_guard(request)
    if guard:
        return guard
    items = db.scalars(select(Assessment).order_by(Assessment.created_at.desc())).all()
    summary = {
        "assessments": len(items),
        "published": sum(1 for a in items if a.status == "published"),
        "submissions": db.scalar(select(func.count(Submission.id))) or 0,
    }
    return render(request, "dashboard.html", {"assessments": items, "summary": summary})


@app.get("/assessments/new", response_class=HTMLResponse)
def new_assessment_page(request: Request):
    guard = trainer_guard(request)
    if guard:
        return guard
    return render(request, "new_assessment.html", {"error": None, "values": {"question_count": 5, "difficulty": "mixed"}})


@app.post("/assessments/generate", response_class=HTMLResponse, dependencies=[Depends(validate_csrf)])
async def generate(request: Request, title: str = Form(""), topic: str = Form(""), instructions: str = Form(""),
                   question_count: int = Form(5), difficulty: str = Form("mixed"), time_limit_minutes: str = Form(""),
                   trainee_group: str = Form(""), allowed_emails: str = Form(""), source_text: str = Form(""),
                   source_file: UploadFile | None = File(None), db: Session = Depends(get_db)):
    guard = trainer_guard(request)
    if guard:
        return guard
    values = {"title": title, "topic": topic, "instructions": instructions, "question_count": question_count,
              "difficulty": difficulty, "time_limit_minutes": time_limit_minutes, "trainee_group": trainee_group,
              "allowed_emails": allowed_emails, "source_text": source_text}
    if not topic.strip():
        return render(request, "new_assessment.html", {"error": "Enter a topic.", "values": values}, 400)
    if question_count < 1 or question_count > 50:
        return render(request, "new_assessment.html", {"error": "Question count must be between 1 and 50.", "values": values}, 400)
    if difficulty not in {"easy", "medium", "hard", "mixed"}:
        return render(request, "new_assessment.html", {"error": "Choose a valid difficulty.", "values": values}, 400)
    if source_file and source_file.filename:
        content = await source_file.read(settings.max_upload_mb * 1024 * 1024 + 1)
        if len(content) > settings.max_upload_mb * 1024 * 1024:
            return render(request, "new_assessment.html", {"error": f"Upload exceeds the {settings.max_upload_mb} MB limit.", "values": values}, 413)
        try:
            extracted = extract_text(source_file.filename, content, settings.max_source_chars)
        except DocumentError as exc:
            return render(request, "new_assessment.html", {"error": str(exc), "values": values}, 400)
        if source_text.strip():
            source_text = source_text.strip() + "\n\n--- Uploaded material ---\n\n" + extracted
        else:
            source_text = extracted
    source_text = source_text.strip()
    if not source_text:
        return render(request, "new_assessment.html", {"error": "Provide source text or upload a supported training document.", "values": values}, 400)
    if len(source_text) > settings.max_source_chars:
        return render(request, "new_assessment.html", {"error": f"Combined source text exceeds {settings.max_source_chars:,} characters.", "values": values}, 400)
    try:
        draft = generate_assessment(topic=topic.strip(), instructions=instructions.strip(), source_text=source_text,
                                    question_count=question_count, difficulty=difficulty, title=title.strip())
    except GenerationError as exc:
        return render(request, "new_assessment.html", {"error": str(exc), "values": values}, 502)
    try:
        limit = int(time_limit_minutes) if time_limit_minutes.strip() else None
        if limit is not None and not 1 <= limit <= 480:
            raise ValueError
    except ValueError:
        return render(request, "new_assessment.html", {"error": "Time limit must be between 1 and 480 minutes.", "values": values}, 400)

    parsed_emails = parse_allowed_emails(allowed_emails)
    allowed_emails_json = json.dumps(parsed_emails) if parsed_emails else None

    item = create_assessment(db, title=title or draft.title, topic=topic, instructions=instructions,
                             difficulty=difficulty, question_count=question_count, time_limit_minutes=limit,
                             trainee_group=trainee_group, source_text=source_text, draft=draft,
                             allowed_emails=allowed_emails_json)
    return RedirectResponse(f"/assessments/{item.id}", status_code=303)


@app.get("/assessments/{assessment_id}", response_class=HTMLResponse)
def assessment_detail(request: Request, assessment_id: int, db: Session = Depends(get_db)):
    guard = trainer_guard(request)
    if guard:
        return guard
    item = get_assessment_or_404(db, assessment_id)
    draft = load_draft(item)
    submissions = db.scalars(select(Submission).where(Submission.assessment_id == item.id).order_by(Submission.submitted_at.desc())).all()

    allowed_list = []
    if item.allowed_emails:
        try:
            allowed_list = json.loads(item.allowed_emails)
        except Exception:
            allowed_list = parse_allowed_emails(item.allowed_emails)
    submitted_ids = {s.trainee_identifier.casefold() for s in submissions}
    pending_emails = [e for e in allowed_list if e.casefold() not in submitted_ids]

    return render(request, "assessment_detail.html", {
        "assessment": item,
        "draft": draft,
        "submissions": submissions,
        "allowed_emails_list": allowed_list,
        "pending_emails": pending_emails,
        "allowed_emails_raw": ", ".join(allowed_list),
        "published_url": f"/a/{item.access_token_hash}" if False else None,
    })


@app.post("/assessments/{assessment_id}/save", dependencies=[Depends(validate_csrf)])
async def save_draft(request: Request, assessment_id: int, db: Session = Depends(get_db)):
    guard = trainer_guard(request)
    if guard:
        return guard
    item = get_assessment_or_404(db, assessment_id)
    if item.status != "draft":
        raise HTTPException(status_code=409, detail="Only draft assessments can be edited. Create a new draft to change a published assessment.")
    form = await request.form()
    old = load_draft(item)
    payload = old.model_dump()
    payload["title"] = str(form.get("title", old.title)).strip()[:200] or old.title
    payload["topic"] = str(form.get("topic", old.topic)).strip()[:300] or old.topic
    for q in payload["questions"]:
        qid = q["id"]
        q["question"] = str(form.get(f"question_{qid}", q["question"])).strip()
        q["explanation"] = str(form.get(f"explanation_{qid}", q["explanation"])).strip()
        q["correct_option_id"] = str(form.get(f"correct_{qid}", q["correct_option_id"]))
        q["difficulty"] = str(form.get(f"difficulty_{qid}", q["difficulty"]))
        for opt in q["options"]:
            opt["text"] = str(form.get(f"option_{qid}_{opt['id']}", opt["text"])).strip()
    try:
        validated = AssessmentDraft.model_validate(payload)
    except Exception:
        return RedirectResponse(f"/assessments/{item.id}?error=validation", status_code=303)
    item.title = validated.title
    item.topic = validated.topic
    item.questions_json = validated.model_dump_json()
    item.question_count = len(validated.questions)

    # Save time limit, allowed emails, and trainee group if provided
    raw_time = str(form.get("time_limit_minutes", "")).strip()
    if raw_time:
        try:
            limit = int(raw_time)
            if 1 <= limit <= 480:
                item.time_limit_minutes = limit
        except ValueError:
            pass
    else:
        item.time_limit_minutes = None

    raw_emails = str(form.get("allowed_emails", "")).strip()
    parsed_emails = parse_allowed_emails(raw_emails)
    item.allowed_emails = json.dumps(parsed_emails) if parsed_emails else None

    if "trainee_group" in form:
        item.trainee_group = str(form.get("trainee_group", "")).strip() or None

    db.commit()
    return RedirectResponse(f"/assessments/{item.id}?saved=1", status_code=303)


@app.post("/assessments/{assessment_id}/regenerate", dependencies=[Depends(validate_csrf)])
def regenerate(request: Request, assessment_id: int, db: Session = Depends(get_db)):
    guard = trainer_guard(request)
    if guard:
        return guard
    item = get_assessment_or_404(db, assessment_id)
    if item.status != "draft":
        raise HTTPException(status_code=409, detail="Published assessments cannot be regenerated.")
    try:
        draft = generate_assessment(topic=item.topic, instructions=item.instructions, source_text=item.source_text,
                                    question_count=item.question_count, difficulty=item.difficulty, title=item.title)
    except GenerationError as exc:
        return RedirectResponse(f"/assessments/{item.id}?error={quote(str(exc))}", status_code=303)
    item.questions_json = draft.model_dump_json()
    item.title = draft.title or item.title
    db.commit()
    return RedirectResponse(f"/assessments/{item.id}?regenerated=1", status_code=303)


@app.post("/assessments/{assessment_id}/publish", dependencies=[Depends(validate_csrf)])
def publish(request: Request, assessment_id: int, db: Session = Depends(get_db)):
    guard = trainer_guard(request)
    if guard:
        return guard
    item = get_assessment_or_404(db, assessment_id)
    if item.status != "draft":
        return RedirectResponse(f"/assessments/{item.id}", status_code=303)
    token = publish_assessment(db, item)
    # The raw token is only shown once in this redirect target's session, never stored in plaintext in DB.
    request.session[f"published_token_{item.id}"] = token
    return RedirectResponse(f"/assessments/{item.id}/published", status_code=303)


@app.post("/assessments/{assessment_id}/rotate-link", dependencies=[Depends(validate_csrf)])
def rotate_link(request: Request, assessment_id: int, db: Session = Depends(get_db)):
    guard = trainer_guard(request)
    if guard:
        return guard
    item = get_assessment_or_404(db, assessment_id)
    if item.status != "published":
        raise HTTPException(status_code=409, detail="Only published assessments can receive a replacement link.")
    token = publish_assessment(db, item)
    request.session[f"published_token_{item.id}"] = token
    return RedirectResponse(f"/assessments/{item.id}/published", status_code=303)


@app.get("/assessments/{assessment_id}/published", response_class=HTMLResponse)
def published_page(request: Request, assessment_id: int, db: Session = Depends(get_db)):
    guard = trainer_guard(request)
    if guard:
        return guard
    item = get_assessment_or_404(db, assessment_id)
    token = request.session.pop(f"published_token_{item.id}", None)
    if not token:
        # On later visits, raw tokens cannot be recovered; explain this instead of exposing hashes.
        return render(request, "published.html", {"assessment": item, "public_url": None, "notice": "The link was shown only once. To issue a replacement link, republish from a new draft."})
    base = str(request.base_url).rstrip("/")
    return render(request, "published.html", {"assessment": item, "public_url": f"{base}/a/{token}", "notice": None})


@app.get("/a/{token}", response_class=HTMLResponse)
def trainee_page(request: Request, token: str, db: Session = Depends(get_db)):
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    item = db.scalar(select(Assessment).where(Assessment.access_token_hash == token_hash(token), Assessment.status == "published"))
    if not item or not item.token_expires_at or item.token_expires_at < now_utc:
        return render(request, "unavailable.html", {"message": "This assessment link is invalid or has expired."}, 404)
    draft = load_draft(item)
    remaining_seconds = None
    total_seconds = None
    if item.time_limit_minutes:
        total_seconds = item.time_limit_minutes * 60
        started = request.session.setdefault(f"assessment_started_{item.id}", datetime.now(timezone.utc).timestamp())
        elapsed = max(0.0, datetime.now(timezone.utc).timestamp() - float(started))
        remaining_seconds = max(0, int(total_seconds - elapsed))
    return render(request, "trainee.html", {
        "assessment": item,
        "questions": safe_questions_for_trainee(draft),
        "remaining_seconds": remaining_seconds,
        "total_seconds": total_seconds,
        "error": None
    })


@app.post("/a/{token}/submit", response_class=HTMLResponse, dependencies=[Depends(validate_csrf)])
async def submit_assessment(request: Request, token: str, trainee_name: str = Form(""), trainee_identifier: str = Form(""),
                            auto_submitted: str = Form("0"), db: Session = Depends(get_db)):
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    item = db.scalar(select(Assessment).where(Assessment.access_token_hash == token_hash(token), Assessment.status == "published"))
    if not item or not item.token_expires_at or item.token_expires_at < now_utc:
        return render(request, "unavailable.html", {"message": "This assessment link is invalid or has expired."}, 404)

    is_auto = str(auto_submitted).strip() == "1"

    if item.time_limit_minutes:
        started = request.session.get(f"assessment_started_{item.id}")
        grace_period = 120  # 120 seconds grace period for network latency and auto-submit
        limit_seconds = item.time_limit_minutes * 60
        elapsed = (datetime.now(timezone.utc).timestamp() - float(started)) if started else 0
        if elapsed > limit_seconds + grace_period:
            return render(request, "unavailable.html", {"message": "The time limit for this assessment has expired. Contact your trainer if you need another attempt."}, 410)

    name = trainee_name.strip()[:200]
    identifier = trainee_identifier.strip().casefold()[:320]

    if is_auto:
        if not name:
            name = "Trainee (Auto-submitted)"
        if not identifier:
            identifier = f"trainee-{secrets.token_hex(4)}@auto-submitted"

    remaining_seconds = 0 if item.time_limit_minutes else None
    total_seconds = item.time_limit_minutes * 60 if item.time_limit_minutes else None

    if not name or not identifier:
        return render(request, "trainee.html", {
            "assessment": item,
            "questions": safe_questions_for_trainee(load_draft(item)),
            "remaining_seconds": remaining_seconds,
            "total_seconds": total_seconds,
            "error": "Enter your name and a unique trainee identifier (usually your email)."
        }, 400)

    if not is_email_allowed(item, identifier):
        return render(request, "trainee.html", {
            "assessment": item,
            "questions": safe_questions_for_trainee(load_draft(item)),
            "remaining_seconds": remaining_seconds,
            "total_seconds": total_seconds,
            "error": f"The email address '{identifier}' is not registered on the trainee list for this assessment. Please contact your trainer."
        }, 403)

    form = await request.form()
    draft = load_draft(item)
    answers = {q.id: str(form.get(f"q_{q.id}", "")) for q in draft.questions}
    score, clean_answers, feedback = grade_submission(draft, answers)
    submission = Submission(assessment_id=item.id, trainee_name=name, trainee_identifier=identifier,
                            answers_json=json.dumps(clean_answers), score=score, total_questions=len(draft.questions),
                            feedback_json=feedback_json(feedback))
    db.add(submission)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return render(request, "unavailable.html", {"message": "A submission already exists for this trainee identifier. Retakes are disabled for this assessment; contact your trainer if you believe this is an error."}, 409)
    return render(request, "results.html", {
        "assessment": item,
        "submission": submission,
        "draft": draft,
        "feedback": feedback,
        "show_explanations": bool(item.show_explanations),
        "was_auto_submitted": is_auto
    })


@app.get("/assessments/{assessment_id}/export.csv")
def export_csv(request: Request, assessment_id: int, db: Session = Depends(get_db)):
    guard = trainer_guard(request)
    if guard:
        return guard
    item = get_assessment_or_404(db, assessment_id)
    submissions = db.scalars(select(Submission).where(Submission.assessment_id == item.id).order_by(Submission.submitted_at.asc())).all()
    csv_text = submissions_csv(item, submissions)
    filename = f"assessment-{item.id}-submissions.csv"
    return Response(content="\ufeff" + csv_text, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
