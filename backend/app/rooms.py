"""
Host API router — create/manage rooms and questions.
All endpoints require a valid Google OAuth JWT (current_host dependency).
"""

import random
import string
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import current_host
from app.leaderboard import mark_room_active, mark_room_inactive, rebuild_room_zset
from app.schemas import (
    QuestionIn,
    ResultRow,
    RoomCreate,
    RoomOut,
    RoomUpdate,
    ScheduleRoom,
)
from app.supabase_client import get_service_supabase

router = APIRouter(prefix="/rooms", tags=["rooms"])

GRACE_MS = 2_000   # extra buffer on top of Σduration for end_at


# ── helpers ───────────────────────────────────────────────────────────────

def _get_room_or_404(room_id: str):
    supa = get_service_supabase()
    res = supa.table("rooms").select("*").eq("id", room_id).single().execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Room not found")
    return res.data


def _assert_owner(room: dict, host_id: str):
    if room["host_id"] != host_id:
        raise HTTPException(status_code=403, detail="Not your room")


def _gen_join_code() -> str:
    return "".join(random.choices(string.digits, k=6))


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


# ── CRUD ──────────────────────────────────────────────────────────────────

@router.post("", response_model=RoomOut, status_code=status.HTTP_201_CREATED)
def create_room(body: RoomCreate, host_id: str = Depends(current_host)):
    supa = get_service_supabase()
    res = supa.table("rooms").insert({
        "host_id": host_id,
        "title": body.title,
    }).execute()
    return res.data[0]


@router.get("/{room_id}", response_model=RoomOut)
def get_room(room_id: str, host_id: str = Depends(current_host)):
    room = _get_room_or_404(room_id)
    _assert_owner(room, host_id)
    return room


@router.put("/{room_id}", response_model=RoomOut)
def update_room(room_id: str, body: RoomUpdate, host_id: str = Depends(current_host)):
    room = _get_room_or_404(room_id)
    _assert_owner(room, host_id)
    if room["status"] not in ("draft", "ready"):
        raise HTTPException(status_code=409, detail="Room cannot be edited after scheduling")

    patch = body.model_dump(exclude_none=True)
    if not patch:
        return room

    patch["updated_at"] = _now_utc().isoformat()
    supa = get_service_supabase()
    res = supa.table("rooms").update(patch).eq("id", room_id).execute()
    return res.data[0]


# ── questions (bulk replace) ───────────────────────────────────────────────

@router.post("/{room_id}/questions", status_code=status.HTTP_204_NO_CONTENT)
def set_questions(
    room_id: str,
    questions: list[QuestionIn],
    host_id: str = Depends(current_host),
):
    room = _get_room_or_404(room_id)
    _assert_owner(room, host_id)
    if room["status"] not in ("draft", "ready"):
        raise HTTPException(status_code=409, detail="Cannot replace questions after scheduling")

    supa = get_service_supabase()
    # delete existing questions for this room, then insert the new set
    supa.table("questions").delete().eq("room_id", room_id).execute()

    if not questions:
        return

    rows = [
        {
            "room_id": room_id,
            "order_index": i,
            "prompt": q.prompt,
            "options": [o.model_dump() for o in q.options],
            "correct_option_ids": q.correct_option_ids,
            "duration_ms": q.duration_ms,
            "base_points": q.base_points,
            "explanation": q.explanation,
        }
        for i, q in enumerate(questions)
    ]
    supa.table("questions").insert(rows).execute()


# ── lifecycle ─────────────────────────────────────────────────────────────

@router.post("/{room_id}/ready", response_model=RoomOut)
def mark_ready(room_id: str, host_id: str = Depends(current_host)):
    """Generate a 6-digit join code and set status = 'ready'."""
    room = _get_room_or_404(room_id)
    _assert_owner(room, host_id)
    if room["status"] != "draft":
        raise HTTPException(status_code=409, detail="Room is not in draft status")

    # Verify there is at least one question
    supa = get_service_supabase()
    q_res = supa.table("questions").select("id").eq("room_id", room_id).limit(1).execute()
    if not q_res.data:
        raise HTTPException(status_code=422, detail="Add at least one question before marking ready")

    # Collision-safe join code: retry on unique constraint violation
    for _ in range(10):
        code = _gen_join_code()
        try:
            res = supa.table("rooms").update({
                "join_code": code,
                "status": "ready",
                "updated_at": _now_utc().isoformat(),
            }).eq("id", room_id).execute()
            return res.data[0]
        except Exception as exc:
            if "uniq_active_join_code" in str(exc):
                continue
            raise

    raise HTTPException(status_code=500, detail="Could not allocate a unique join code; try again")


@router.post("/{room_id}/schedule", response_model=RoomOut)
def schedule_room(room_id: str, body: ScheduleRoom, host_id: str = Depends(current_host)):
    room = _get_room_or_404(room_id)
    _assert_owner(room, host_id)
    if room["status"] != "ready":
        raise HTTPException(status_code=409, detail="Room must be in 'ready' status to schedule")

    supa = get_service_supabase()
    res = supa.table("rooms").update({
        "status": "scheduled",
        "start_at": body.start_at.isoformat(),
        "updated_at": _now_utc().isoformat(),
    }).eq("id", room_id).execute()
    return res.data[0]


@router.post("/{room_id}/start", response_model=RoomOut)
def start_room(room_id: str, host_id: str = Depends(current_host)):
    """Immediate start: T0 = now. Calculates end_at from question durations."""
    room = _get_room_or_404(room_id)
    _assert_owner(room, host_id)
    if room["status"] not in ("ready", "scheduled"):
        raise HTTPException(status_code=409, detail="Room must be 'ready' or 'scheduled' to start")

    supa = get_service_supabase()
    q_res = supa.table("questions").select("duration_ms").eq("room_id", room_id).execute()
    if not q_res.data:
        raise HTTPException(status_code=422, detail="No questions in room")

    total_ms = sum(q["duration_ms"] for q in q_res.data)
    now = _now_utc()
    # end_at = T0 + Σduration + grace
    from datetime import timedelta
    end_at = now + timedelta(milliseconds=total_ms + GRACE_MS)

    res = supa.table("rooms").update({
        "status": "live",
        "start_at": now.isoformat(),
        "end_at": end_at.isoformat(),
        "updated_at": now.isoformat(),
    }).eq("id", room_id).execute()

    # Broadcaster starts ticking this room; seed the board with lobby joiners
    mark_room_active(room_id)
    rebuild_room_zset(room_id)

    return res.data[0]


@router.post("/{room_id}/end", response_model=RoomOut)
def end_room(room_id: str, host_id: str = Depends(current_host)):
    """Close the room and sweep all unanswered questions → 0 points."""
    room = _get_room_or_404(room_id)
    _assert_owner(room, host_id)
    if room["status"] != "live":
        raise HTTPException(status_code=409, detail="Room is not live")

    supa = get_service_supabase()
    # Sweep first (idempotent), then close
    supa.rpc("sweep_room", {"p_room": room_id}).execute()

    res = supa.table("rooms").update({
        "status": "closed",
        "updated_at": _now_utc().isoformat(),
    }).eq("id", room_id).execute()

    mark_room_inactive(room_id)

    return res.data[0]


# ── results ───────────────────────────────────────────────────────────────

@router.get("/{room_id}/results", response_model=list[ResultRow])
def get_results(room_id: str, host_id: str = Depends(current_host)):
    """Live standings from participants table (score DESC, time ASC)."""
    room = _get_room_or_404(room_id)
    _assert_owner(room, host_id)

    supa = get_service_supabase()
    res = (
        supa.table("participants")
        .select("id, display_name, status, score_total, time_total_ms")
        .eq("room_id", room_id)
        .order("score_total", desc=True)
        .order("time_total_ms", desc=False)
        .execute()
    )

    return [
        ResultRow(
            rank=i + 1,
            participant_id=p["id"],
            display_name=p["display_name"],
            score_total=p["score_total"],
            time_total_ms=p["time_total_ms"],
            status=p["status"],
        )
        for i, p in enumerate(res.data)
    ]
