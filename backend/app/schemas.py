"""
Pydantic request/response models.
correct_option_ids is NEVER included in any response model, with one
deliberate exception: ReviewQuestion, served only after finalization.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Room ──────────────────────────────────────────────────────────────────

class OptionIn(BaseModel):
    id: str
    text: str


class QuestionIn(BaseModel):
    prompt: str
    options: list[OptionIn]
    correct_option_ids: list[str]    # received from host; stored server-side only
    duration_ms: int = Field(gt=0)
    base_points: int = Field(default=1000, gt=0)
    explanation: str | None = None


class RoomCreate(BaseModel):
    title: str


class RoomUpdate(BaseModel):
    title: str | None = None
    scoring_config: dict[str, Any] | None = None
    allow_late_join: bool | None = None


class ScheduleRoom(BaseModel):
    start_at: datetime


# ── Room response (safe — no secrets) ─────────────────────────────────────

class RoomOut(BaseModel):
    id: str
    title: str
    status: str
    join_code: str | None
    start_at: datetime | None
    end_at: datetime | None
    created_at: datetime


# ── Play ──────────────────────────────────────────────────────────────────

class JoinRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)
    display_name: str = Field(min_length=1, max_length=64)


class JoinResponse(BaseModel):
    session_token: str
    participant_id: str
    room_id: str
    room_status: str


class NextRequest(BaseModel):
    session_token: str


class OptionOut(BaseModel):
    id: str
    text: str


class QuestionOut(BaseModel):
    """Question payload sent to participants — NO correct_option_ids."""
    question_id: str
    order_index: int
    prompt: str
    options: list[OptionOut]
    duration_ms: int
    base_points: int
    deadline: datetime       # absolute server-side deadline
    server_now: datetime     # client uses this to compute clock offset


class DoneResponse(BaseModel):
    done: bool = True
    message: str = "You have completed all questions."


class AnswerRequest(BaseModel):
    session_token: str
    question_id: str
    option_id: str | None = None   # null = intentional no-answer


class AnswerResponse(BaseModel):
    recorded: bool
    message: str


# ── Results ───────────────────────────────────────────────────────────────

class ResultRow(BaseModel):
    rank: int
    participant_id: str
    display_name: str
    score_total: int
    time_total_ms: int
    status: str


class FinalStanding(BaseModel):
    rank: int
    participant_id: str
    display_name: str
    score_total: int
    time_total_ms: int


class ReviewQuestion(BaseModel):
    """Post-finalization only — the one place correct answers are revealed."""
    question_id: str
    order_index: int
    prompt: str
    options: list[OptionOut]
    correct_option_ids: list[str]
    explanation: str | None = None
    your_option_id: str | None = None
    is_correct: bool | None = None
    points_awarded: int | None = None


class ReviewResponse(BaseModel):
    results: list[FinalStanding]
    questions: list[ReviewQuestion]


class MeResponse(BaseModel):
    participant_id: str
    display_name: str
    status: str
    score_total: int
    time_total_ms: int
    current_question_index: int
    rank: int | None = None
    room_status: str = ""
