from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Difficulty = Literal["easy", "medium", "hard"]


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Literal["A", "B", "C", "D"]
    text: str = Field(min_length=1, max_length=1000)


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=40)
    question: str = Field(min_length=1, max_length=3000)
    options: list[Option]
    correct_option_id: Literal["A", "B", "C", "D"]
    explanation: str = Field(min_length=1, max_length=3000)
    difficulty: Difficulty
    source_reference: str = Field(default="", max_length=500)

    @field_validator("options")
    @classmethod
    def validate_options(cls, options: list[Option]) -> list[Option]:
        if len(options) != 4:
            raise ValueError("Each question must have exactly four options")
        ids = [o.id for o in options]
        texts = [o.text.strip().casefold() for o in options]
        if len(set(ids)) != 4 or len(set(texts)) != 4:
            raise ValueError("Option IDs and option texts must be distinct")
        return options


class AssessmentDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    topic: str = Field(min_length=1, max_length=300)
    questions: list[Question] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_question_ids(self):
        ids = [q.id for q in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("Question IDs must be unique")
        return self
