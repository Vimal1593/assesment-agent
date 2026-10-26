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


def test_extract_json_payload():
    from app.question_generator import extract_json_payload
    # Plain JSON
    assert extract_json_payload('{"key": "value"}') == '{"key": "value"}'
    # Markdown code fence
    assert extract_json_payload('```json\n{"key": "value"}\n```') == '{"key": "value"}'
    # Markdown code fence with extra conversation
    text = 'Here is your draft:\n```json\n{"title": "Test", "topic": "T", "questions": []}\n```\nHope it helps!'
    assert extract_json_payload(text) == '{"title": "Test", "topic": "T", "questions": []}'


def test_ollama_generation_success(monkeypatch):
    import json
    import httpx
    from app.question_generator import generate_assessment

    valid_response_data = {
        "model": "qwen2.5:7b",
        "message": {
            "role": "assistant",
            "content": json.dumps({
                "title": "Python Basics",
                "topic": "Python",
                "questions": [
                    {
                        "id": "q1",
                        "question": "What is Python?",
                        "options": [
                            {"id": "A", "text": "A programming language"},
                            {"id": "B", "text": "A reptile species only"},
                            {"id": "C", "text": "A car brand"},
                            {"id": "D", "text": "A coffee brand"}
                        ],
                        "correct_option_id": "A",
                        "explanation": "Python is a high-level programming language.",
                        "difficulty": "easy",
                        "source_reference": "Section 1"
                    }
                ]
            })
        },
        "done": True
    }

    class MockResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        def json(self):
            return valid_response_data
        def raise_for_status(self):
            pass

    def mock_post(self, url, json=None, **kwargs):
        return MockResponse()

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    draft = generate_assessment(
        topic="Python",
        instructions="Focus on basics",
        source_text="Python is a programming language.",
        question_count=1,
        difficulty="easy"
    )

    assert draft.title == "Python Basics"
    assert len(draft.questions) == 1
    assert draft.questions[0].id == "q1"
    assert draft.questions[0].correct_option_id == "A"


def test_ollama_generation_malformed_output(monkeypatch):
    import httpx
    from app.question_generator import generate_assessment, GenerationError

    class MockMalformedResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        def json(self):
            return {"message": {"content": "I cannot generate questions from this text."}}
        def raise_for_status(self):
            pass

    def mock_post(self, url, json=None, **kwargs):
        return MockMalformedResponse()

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    with pytest.raises(GenerationError) as exc_info:
        generate_assessment(
            topic="Test",
            instructions="",
            source_text="Some source text",
            question_count=5,
            difficulty="mixed"
        )
    assert "validation" in str(exc_info.value).lower() or "json" in str(exc_info.value).lower()


def test_ollama_generation_schema_violation(monkeypatch):
    import json
    import httpx
    from app.question_generator import generate_assessment, GenerationError

    # Schema violation: only 2 options instead of exactly 4
    invalid_schema_data = {
        "title": "Invalid",
        "topic": "Test",
        "questions": [
            {
                "id": "q1",
                "question": "Question with too few options?",
                "options": [
                    {"id": "A", "text": "Opt 1"},
                    {"id": "B", "text": "Opt 2"}
                ],
                "correct_option_id": "A",
                "explanation": "Exp",
                "difficulty": "easy",
                "source_reference": ""
            }
        ]
    }

    class MockViolationResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        def json(self):
            return {"message": {"content": json.dumps(invalid_schema_data)}}
        def raise_for_status(self):
            pass

    def mock_post(self, url, json=None, **kwargs):
        return MockViolationResponse()

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    with pytest.raises(GenerationError) as exc_info:
        generate_assessment(
            topic="Test",
            instructions="",
            source_text="Some text",
            question_count=5,
            difficulty="easy"
        )
    assert "validation" in str(exc_info.value).lower()


def test_ollama_generation_connection_failure(monkeypatch):
    import httpx
    from app.question_generator import generate_assessment, GenerationError

    def mock_post_connect_error(self, url, json=None, **kwargs):
        raise httpx.ConnectError("Connection refused by target host")

    monkeypatch.setattr(httpx.Client, "post", mock_post_connect_error)

    with pytest.raises(GenerationError) as exc_info:
        generate_assessment(
            topic="Test",
            instructions="",
            source_text="Some text",
            question_count=5,
            difficulty="easy"
        )
    assert "could not connect to ollama" in str(exc_info.value).lower()


def test_ollama_generation_timeout(monkeypatch):
    import httpx
    from app.question_generator import generate_assessment, GenerationError

    def mock_post_timeout(self, url, json=None, **kwargs):
        raise httpx.ReadTimeout("Read timed out")

    monkeypatch.setattr(httpx.Client, "post", mock_post_timeout)

    with pytest.raises(GenerationError) as exc_info:
        generate_assessment(
            topic="Test",
            instructions="",
            source_text="Some text",
            question_count=5,
            difficulty="easy"
        )
    assert "timed out" in str(exc_info.value).lower()


def test_ollama_generation_model_not_found(monkeypatch):
    import httpx
    from app.question_generator import generate_assessment, GenerationError

    class Mock404Response:
        status_code = 404
        headers = {"content-type": "application/json"}
        def json(self):
            return {"error": "model 'qwen2.5:0.5b-instruct' not found, try pulling it first"}
        def raise_for_status(self):
            raise httpx.HTTPStatusError("404 Not Found", request=None, response=self)

    def mock_post_404(self, url, json=None, **kwargs):
        return Mock404Response()

    monkeypatch.setattr(httpx.Client, "post", mock_post_404)

    with pytest.raises(GenerationError) as exc_info:
        generate_assessment(
            topic="Test",
            instructions="",
            source_text="Some text",
            question_count=5,
            difficulty="easy"
        )
    assert "was not found" in str(exc_info.value).lower()


def test_provider_selection_ollama_does_not_require_openai_key(monkeypatch):
    import json
    import httpx
    from app.config import get_settings
    from app.question_generator import generate_assessment

    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()

    valid_response_data = {
        "model": "qwen2.5:0.5b-instruct",
        "message": {
            "role": "assistant",
            "content": json.dumps({
                "title": "Cloud Fundamentals",
                "topic": "Cloud",
                "questions": [
                    {
                        "id": "q1",
                        "question": "What does IaaS stand for?",
                        "options": [
                            {"id": "A", "text": "Infrastructure as a Service"},
                            {"id": "B", "text": "Internet as a Service"},
                            {"id": "C", "text": "Integration as a Service"},
                            {"id": "D", "text": "Identity as a Service"}
                        ],
                        "correct_option_id": "A",
                        "explanation": "IaaS stands for Infrastructure as a Service.",
                        "difficulty": "easy",
                        "source_reference": "Slide 2"
                    }
                ]
            })
        }
    }

    class MockResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        def json(self):
            return valid_response_data
        def raise_for_status(self):
            pass

    monkeypatch.setattr(httpx.Client, "post", lambda self, url, json=None, **kwargs: MockResponse())

    draft = generate_assessment(
        topic="Cloud",
        instructions="",
        source_text="IaaS is Infrastructure as a Service.",
        question_count=1,
        difficulty="easy"
    )
    assert draft.title == "Cloud Fundamentals"
    assert len(draft.questions) == 1
    assert draft.questions[0].correct_option_id == "A"
    get_settings.cache_clear()


def test_provider_selection_openai_fallback_missing_key(monkeypatch):
    from app.config import get_settings
    from app.question_generator import generate_assessment, GenerationError

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()

    with pytest.raises(GenerationError) as exc_info:
        generate_assessment(
            topic="Test",
            instructions="",
            source_text="Some text",
            question_count=1,
            difficulty="easy"
        )
    assert "openai api key is not configured" in str(exc_info.value).lower()
    get_settings.cache_clear()


def test_provider_selection_openai_fallback_success(monkeypatch):
    import json
    from types import SimpleNamespace
    from app.config import get_settings
    from app.question_generator import generate_assessment

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-mock-key-for-test")
    get_settings.cache_clear()

    valid_json = json.dumps({
        "title": "OpenAI Generated Draft",
        "topic": "Python",
        "questions": [
            {
                "id": "q1",
                "question": "What is Python?",
                "options": [
                    {"id": "A", "text": "Language"},
                    {"id": "B", "text": "Snake only"},
                    {"id": "C", "text": "Vehicle"},
                    {"id": "D", "text": "Food"}
                ],
                "correct_option_id": "A",
                "explanation": "Python is a language.",
                "difficulty": "medium",
                "source_reference": "Doc"
            }
        ]
    })

    class MockResponses:
        def create(self, **kwargs):
            return SimpleNamespace(output_text=valid_json)

    class MockOpenAIClient:
        def __init__(self, **kwargs):
            self.responses = MockResponses()

    import openai
    monkeypatch.setattr(openai, "OpenAI", MockOpenAIClient)

    draft = generate_assessment(
        topic="Python",
        instructions="",
        source_text="Python is a language.",
        question_count=1,
        difficulty="medium"
    )
    assert draft.title == "OpenAI Generated Draft"
    assert len(draft.questions) == 1
    assert draft.questions[0].correct_option_id == "A"
    get_settings.cache_clear()


def make_mock_question(idx: int, topic: str = "Test"):
    return {
        "id": f"q{idx}",
        "question": f"What is concept {idx} in {topic}?",
        "options": [
            {"id": "A", "text": f"Correct option for {idx}"},
            {"id": "B", "text": f"Distractor B for {idx}"},
            {"id": "C", "text": f"Distractor C for {idx}"},
            {"id": "D", "text": f"Distractor D for {idx}"},
        ],
        "correct_option_id": "A",
        "explanation": f"Explanation for question {idx}",
        "difficulty": "medium",
        "source_reference": f"Section {idx}",
    }


def test_ollama_generation_10_questions_success(monkeypatch):
    import json
    import httpx
    from app.question_generator import generate_assessment

    questions_10 = [make_mock_question(i, "Python") for i in range(1, 11)]
    response_payload = {
        "model": "qwen2.5:0.5b-instruct",
        "done": True,
        "done_reason": "stop",
        "eval_count": 1800,
        "prompt_eval_count": 250,
        "message": {
            "role": "assistant",
            "content": json.dumps({
                "title": "Python 10 Questions",
                "topic": "Python",
                "questions": questions_10,
            }),
        },
    }

    class MockResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        def json(self):
            return response_payload
        def raise_for_status(self):
            pass

    monkeypatch.setattr(httpx.Client, "post", lambda self, url, json=None, **kwargs: MockResponse())

    draft = generate_assessment(
        topic="Python",
        instructions="",
        source_text="Python guide with 10 concepts.",
        question_count=10,
        difficulty="medium",
    )
    assert len(draft.questions) == 10
    assert draft.questions[0].id == "q1"
    assert draft.questions[9].id == "q10"


def test_ollama_generation_counts_1_5_10(monkeypatch):
    import json
    import httpx
    from app.question_generator import generate_assessment

    for count in (1, 5, 10):
        questions = [make_mock_question(i, "Cloud") for i in range(1, count + 1)]
        resp_data = {
            "model": "qwen2.5:0.5b-instruct",
            "done": True,
            "done_reason": "stop",
            "message": {
                "role": "assistant",
                "content": json.dumps({"title": "Cloud", "topic": "Cloud", "questions": questions}),
            },
        }

        class MockResp:
            status_code = 200
            headers = {"content-type": "application/json"}
            def json(self):
                return resp_data
            def raise_for_status(self):
                pass

        monkeypatch.setattr(httpx.Client, "post", lambda self, url, json=None, **kwargs: MockResp())

        draft = generate_assessment(
            topic="Cloud",
            instructions="",
            source_text="Cloud source material.",
            question_count=count,
            difficulty="mixed",
        )
        assert len(draft.questions) == count


def test_ollama_generation_continuation_when_fewer_questions_returned(monkeypatch):
    import json
    import httpx
    from app.question_generator import generate_assessment

    # First call returns only 1 question when 10 were requested
    initial_questions = [make_mock_question(1, "DevOps")]
    # Continuation call returns remaining 9 questions
    continuation_questions = [make_mock_question(i, "DevOps") for i in range(2, 11)]

    call_count = 0

    class MockSequenceResponse:
        def __init__(self, data):
            self._data = data
            self.status_code = 200
            self.headers = {"content-type": "application/json"}
        def json(self):
            return self._data
        def raise_for_status(self):
            pass

    def mock_post_sequence(self, url, json=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call yields only 1 question
            return MockSequenceResponse({
                "model": "qwen2.5:0.5b-instruct",
                "done": True,
                "done_reason": "stop",
                "eval_count": 200,
                "message": {
                    "role": "assistant",
                    "content": json_dumps({"title": "DevOps", "topic": "DevOps", "questions": initial_questions}),
                },
            })
        else:
            # Continuation call yields remaining 9 questions
            return MockSequenceResponse({
                "model": "qwen2.5:0.5b-instruct",
                "done": True,
                "done_reason": "stop",
                "eval_count": 1600,
                "message": {
                    "role": "assistant",
                    "content": json_dumps({"title": "DevOps", "topic": "DevOps", "questions": continuation_questions}),
                },
            })

    json_dumps = json.dumps
    monkeypatch.setattr(httpx.Client, "post", mock_post_sequence)

    draft = generate_assessment(
        topic="DevOps",
        instructions="",
        source_text="Extensive DevOps manual.",
        question_count=10,
        difficulty="medium",
    )
    assert call_count == 2
    assert len(draft.questions) == 10
    assert draft.questions[0].id == "q1"
    assert draft.questions[9].id == "q10"


def test_ollama_generation_continuation_deduplication(monkeypatch):
    import json
    import httpx
    from app.question_generator import generate_assessment

    # Initial call returns 2 questions
    q1 = make_mock_question(1, "Security")
    q2 = make_mock_question(2, "Security")
    initial_questions = [q1, q2]

    # Continuation returns 1 duplicate of q1 and 1 new question q3
    q1_dup = make_mock_question(1, "Security")
    q1_dup["question"] = "What is concept 1 in Security?"  # duplicate text
    q3 = make_mock_question(3, "Security")
    continuation_questions = [q1_dup, q3]

    call_count = 0

    class MockResp:
        def __init__(self, data):
            self._data = data
            self.status_code = 200
            self.headers = {"content-type": "application/json"}
        def json(self):
            return self._data
        def raise_for_status(self):
            pass

    def mock_post(self, url, json_payload=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return MockResp({
                "model": "qwen2.5:0.5b-instruct",
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": json.dumps({"title": "Sec", "topic": "Sec", "questions": initial_questions}),
                },
            })
        return MockResp({
            "model": "qwen2.5:0.5b-instruct",
            "done": True,
            "message": {
                "role": "assistant",
                "content": json.dumps({"title": "Sec", "topic": "Sec", "questions": continuation_questions}),
            },
        })

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    draft = generate_assessment(
        topic="Security",
        instructions="",
        source_text="Security basics.",
        question_count=3,
        difficulty="medium",
    )
    assert len(draft.questions) == 3
    # Check that q1 was not added twice
    texts = [q.question for q in draft.questions]
    assert len(set(texts)) == 3


def test_ollama_generation_insufficient_source_material_raises_error(monkeypatch):
    import json
    import httpx
    from app.question_generator import generate_assessment, GenerationError

    # Both initial and continuation return only 1 question each (total 2 < 10)
    q1 = make_mock_question(1, "Brief")
    q2 = make_mock_question(2, "Brief")
    call_count = 0

    class MockResp:
        status_code = 200
        headers = {"content-type": "application/json"}
        def __init__(self, q):
            self.q = q
        def json(self):
            return {
                "model": "qwen2.5:0.5b-instruct",
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": json.dumps({"title": "Brief", "topic": "Brief", "questions": [self.q]}),
                },
            }
        def raise_for_status(self):
            pass

    def mock_post(self, url, json=None, **kwargs):
        nonlocal call_count
        call_count += 1
        return MockResp(q1 if call_count == 1 else q2)

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    with pytest.raises(GenerationError) as exc_info:
        generate_assessment(
            topic="Brief",
            instructions="",
            source_text="Only one brief sentence.",
            question_count=10,
            difficulty="easy",
        )

    err = str(exc_info.value).lower()
    assert "generated only 2 of the 10 requested questions" in err
    assert "too brief" in err


def test_ollama_generation_token_truncation_done_reason_length(monkeypatch):
    import httpx
    from app.question_generator import generate_assessment, GenerationError

    # Model was truncated mid-JSON due to output length limit
    class MockTruncatedResponse:
        status_code = 200
        headers = {"content-type": "application/json"}
        def json(self):
            return {
                "model": "qwen2.5:0.5b-instruct",
                "done": True,
                "done_reason": "length",
                "eval_count": 2500,
                "message": {
                    "role": "assistant",
                    "content": '{"title": "Truncated", "topic": "T", "questions": [{"id": "q1", "question": "Incomplete',
                },
            }
        def raise_for_status(self):
            pass

    monkeypatch.setattr(httpx.Client, "post", lambda self, url, json=None, **kwargs: MockTruncatedResponse())

    with pytest.raises(GenerationError) as exc_info:
        generate_assessment(
            topic="Test",
            instructions="",
            source_text="Some source text",
            question_count=10,
            difficulty="medium",
        )
    assert "validation" in str(exc_info.value).lower() or "json" in str(exc_info.value).lower()


def test_ollama_generation_applies_num_predict_and_num_ctx(monkeypatch):
    import json
    import httpx
    from app.config import get_settings
    from app.question_generator import generate_assessment

    monkeypatch.setenv("OLLAMA_NUM_PREDICT", "3200")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "8192")
    get_settings.cache_clear()

    captured_payload = {}

    class MockResp:
        status_code = 200
        headers = {"content-type": "application/json"}
        def json(self):
            return {
                "model": "qwen2.5:0.5b-instruct",
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": json.dumps({"title": "T", "topic": "T", "questions": [make_mock_question(1)]}),
                },
            }
        def raise_for_status(self):
            pass

    def mock_post(self, url, json=None, **kwargs):
        nonlocal captured_payload
        captured_payload = json
        return MockResp()

    monkeypatch.setattr(httpx.Client, "post", mock_post)

    draft = generate_assessment(
        topic="T",
        instructions="",
        source_text="Test source.",
        question_count=1,
        difficulty="easy",
    )
    assert len(draft.questions) == 1
    assert captured_payload["options"]["num_predict"] == 3200
    assert captured_payload["options"]["num_ctx"] == 8192
    get_settings.cache_clear()

