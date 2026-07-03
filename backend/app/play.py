"""
Participant (play) API router — anonymous, service-role access.
Session tokens are issued at join and required on all subsequent calls.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status

from app.schemas import (
    AnswerRequest,
    AnswerResponse,
    DoneResponse,
    JoinRequest,
    JoinResponse,
    MeResponse,
    NextRequest,
    QuestionOut,
)
from app.scoring import score as compute_score
from app.supabase_client import get_service_supabase

router = APIRouter(tags=["play"])

GRACE_MS = 500   # server-side grace window for late answers (ms)


# ── helpers ───────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _get_room_by_code(code: str) -> dict:
    supa = get_service_supabase()
    res = supa.table("rooms").select("*").eq("join_code", code).single().execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Room not found")
    return res.data


def _get_participant(room_id: str, session_token: str) -> dict:
    supa = get_service_supabase()
    res = (
        supa.table("participants")
        .select("*")
        .eq("room_id", room_id)
        .eq("session_token", session_token)
        .single()
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=401, detail="Invalid session token")
    return res.data


def _get_question_by_index(room_id: str, order_index: int) -> dict | None:
    supa = get_service_supabase()
    res = (
        supa.table("questions")
        .select("*")
        .eq("room_id", room_id)
        .eq("order_index", order_index)
        .single()
        .execute()
    )
    return res.data  # None if not found


def _question_count(room_id: str) -> int:
    supa = get_service_supabase()
    res = supa.table("questions").select("id", count="exact").eq("room_id", room_id).execute()
    return res.count or 0


def _get_rank(room_id: str, participant_id: str) -> int | None:
    """Returns 1-based rank among participants in this room."""
    supa = get_service_supabase()
    res = (
        supa.table("participants")
        .select("id, score_total, time_total_ms")
        .eq("room_id", room_id)
        .order("score_total", desc=True)
        .order("time_total_ms", desc=False)
        .execute()
    )
    for i, p in enumerate(res.data or []):
        if p["id"] == participant_id:
            return i + 1
    return None


# ── join ──────────────────────────────────────────────────────────────────

@router.post("/join", response_model=JoinResponse, status_code=status.HTTP_201_CREATED)
def join_room(body: JoinRequest):
    room = _get_room_by_code(body.code)
    if room["status"] not in ("ready", "live", "scheduled"):
        raise HTTPException(status_code=409, detail="Room is not accepting participants")
    if room["status"] == "live" and not room["allow_late_join"]:
        raise HTTPException(status_code=409, detail="Room has already started; late join is off")

    supa = get_service_supabase()
    res = supa.table("participants").insert({
        "room_id": room["id"],
        "display_name": body.display_name,
        "status": "in_lobby",
    }).execute()

    p = res.data[0]
    return JoinResponse(
        session_token=str(p["session_token"]),
        participant_id=str(p["id"]),
        room_id=str(room["id"]),
        room_status=room["status"],
    )


# ── next ──────────────────────────────────────────────────────────────────

@router.post("/rooms/{code}/next")
def next_question(code: str, body: NextRequest):
    """
    Returns the participant's current question with absolute deadline + server_now.
    Idempotent: calling again while a question is open returns the same deadline.
    Auto-records 0 for expired questions and advances to the next one.
    Returns DoneResponse when all questions are completed.
    """
    room = _get_room_by_code(code)
    if room["status"] != "live":
        raise HTTPException(status_code=409, detail="Room is not live")

    now = _now_utc()
    room_end = datetime.fromisoformat(room["end_at"])
    if room_end.tzinfo is None:
        room_end = room_end.replace(tzinfo=timezone.utc)
    if now > room_end:
        raise HTTPException(status_code=409, detail="Room has ended")

    supa = get_service_supabase()
    participant = _get_participant(room["id"], body.session_token)

    if participant["status"] == "finished":
        return DoneResponse()

    total_questions = _question_count(room["id"])

    # Advance through expired questions
    while participant["current_question_index"] < total_questions:
        idx = participant["current_question_index"]
        question = _get_question_by_index(room["id"], idx)
        if question is None:
            break

        # Get (or create) the canonical started_at for this question
        started_at_raw = supa.rpc("start_question", {
            "p_participant": participant["id"],
            "p_question": question["id"],
        }).execute().data

        # Parse started_at
        if isinstance(started_at_raw, str):
            started_at = datetime.fromisoformat(started_at_raw)
        else:
            started_at = datetime.fromisoformat(str(started_at_raw))
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)

        deadline = started_at + timedelta(milliseconds=question["duration_ms"])
        grace_deadline = deadline + timedelta(milliseconds=GRACE_MS)

        if now <= grace_deadline:
            # Question is still open — return it (no correct answers)
            # Activate participant if first question
            if participant["status"] == "in_lobby":
                supa.table("participants").update({"status": "active"}).eq(
                    "id", participant["id"]
                ).execute()

            return QuestionOut(
                question_id=str(question["id"]),
                order_index=question["order_index"],
                prompt=question["prompt"],
                options=question["options"],  # already [{"id":...,"text":...}]
                duration_ms=question["duration_ms"],
                base_points=question["base_points"],
                deadline=deadline,
                server_now=now,
            )

        # Question has expired without an answer → record 0 and advance
        latency_ms = question["duration_ms"]
        supa.rpc("record_answer", {
            "p_room": room["id"],
            "p_participant": participant["id"],
            "p_question": question["id"],
            "p_selected": None,
            "p_started": started_at.isoformat(),
            "p_submitted": now.isoformat(),
            "p_latency_ms": latency_ms,
            "p_is_correct": False,
            "p_points": 0,
        }).execute()

        # Refresh participant state (index was advanced by record_answer)
        participant = _get_participant(room["id"], body.session_token)

    # All questions done — mark finished
    supa.table("participants").update({
        "status": "finished",
        "finished_at": now.isoformat(),
    }).eq("id", participant["id"]).execute()

    return DoneResponse()


# ── answer ────────────────────────────────────────────────────────────────

@router.post("/rooms/{code}/answer", response_model=AnswerResponse)
def submit_answer(code: str, body: AnswerRequest):
    room = _get_room_by_code(code)
    if room["status"] != "live":
        raise HTTPException(status_code=409, detail="Room is not live")

    supa = get_service_supabase()
    participant = _get_participant(room["id"], body.session_token)

    if participant["status"] == "finished":
        raise HTTPException(status_code=409, detail="You have already finished")

    # Fetch the question the participant is currently on
    idx = participant["current_question_index"]
    question = _get_question_by_index(room["id"], idx)
    if question is None:
        raise HTTPException(status_code=409, detail="No active question")

    if str(question["id"]) != body.question_id:
        raise HTTPException(status_code=409, detail="question_id does not match your current question")

    # Get started_at (must already exist — /next must have been called first)
    qs_res = (
        supa.table("question_starts")
        .select("started_at")
        .eq("participant_id", participant["id"])
        .eq("question_id", question["id"])
        .single()
        .execute()
    )
    if not qs_res.data:
        raise HTTPException(status_code=409, detail="Call /next before /answer")

    started_at_raw = qs_res.data["started_at"]
    started_at = datetime.fromisoformat(started_at_raw)
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)

    now = _now_utc()
    deadline = started_at + timedelta(milliseconds=question["duration_ms"])
    grace_deadline = deadline + timedelta(milliseconds=GRACE_MS)

    # Determine correctness and score
    expired = now > grace_deadline
    if expired or body.option_id is None:
        is_correct = False
        points = 0
        latency_ms = question["duration_ms"]
        selected = None
    else:
        correct_ids: list = question["correct_option_ids"]
        is_correct = body.option_id in correct_ids
        latency_ms = max(0, int((now - started_at).total_seconds() * 1000))
        points = compute_score(is_correct, latency_ms, question["duration_ms"], question["base_points"])
        selected = body.option_id

    supa.rpc("record_answer", {
        "p_room": room["id"],
        "p_participant": participant["id"],
        "p_question": str(question["id"]),
        "p_selected": selected,
        "p_started": started_at.isoformat(),
        "p_submitted": now.isoformat(),
        "p_latency_ms": latency_ms,
        "p_is_correct": is_correct,
        "p_points": points,
    }).execute()

    msg = "Expired — recorded as 0" if expired else "Recorded"
    return AnswerResponse(recorded=True, message=msg)


# ── me ────────────────────────────────────────────────────────────────────

@router.get("/rooms/{code}/me", response_model=MeResponse)
def get_me(code: str, token: str):
    """Participant self-view: current state + rank."""
    room = _get_room_by_code(code)
    participant = _get_participant(room["id"], token)
    rank = _get_rank(room["id"], participant["id"])
    return MeResponse(
        participant_id=str(participant["id"]),
        display_name=participant["display_name"],
        status=participant["status"],
        score_total=participant["score_total"],
        time_total_ms=participant["time_total_ms"],
        current_question_index=participant["current_question_index"],
        rank=rank,
        room_status=room["status"],
    )
