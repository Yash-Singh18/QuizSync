"""
Room lifecycle transitions — shared by the host API ("Start now") and the
background worker (T0 flip / T_end close / finalize).

Every transition is a guarded UPDATE (WHERE status = ...), so a double fire —
web + worker racing, or a worker restarted mid-contest — is a harmless no-op.
"""

import json
import time
from datetime import datetime, timedelta, timezone

from app import leaderboard as lb
from app.redis_client import get_redis
from app.supabase_client import get_service_supabase

END_GRACE_MS = 2_000   # extra buffer on top of Σduration for end_at
FINAL_TOP_K = 20


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def go_live(room_id: str) -> dict | None:
    """
    ready|scheduled → live. T0 = now; end_at = T0 + Σduration + grace.
    Pre-stamps Q1's started_at for every participant (HSETNX) so answers can
    flow straight from the `go` push — no /next stampede at T0.
    Returns the live room, or None if another caller already transitioned it.
    """
    supa = get_service_supabase()
    questions = (
        supa.table("questions")
        .select("*")
        .eq("room_id", room_id)
        .order("order_index")
        .execute()
        .data
    )
    if not questions:
        return None

    now = _now_utc()
    total_ms = sum(q["duration_ms"] for q in questions)
    end_at = now + timedelta(milliseconds=total_ms + END_GRACE_MS)

    res = (
        supa.table("rooms")
        .update({
            "status": "live",
            "start_at": now.isoformat(),
            "end_at": end_at.isoformat(),
            "updated_at": now.isoformat(),
        })
        .eq("id", room_id)
        .in_("status", ["ready", "scheduled"])
        .execute()
    )
    if not res.data:
        return None
    room = res.data[0]

    # Broadcaster starts ticking this room; seed the board with lobby joiners.
    lb.mark_room_active(room_id)
    lb.rebuild_room_zset(room_id)

    # The room is live now, so joins are blocked (late join off) and this
    # participant set is final.
    q1 = questions[0]
    participants = (
        supa.table("participants").select("id").eq("room_id", room_id).execute().data or []
    )
    r = get_redis()
    if participants:
        pipe = r.pipeline()
        for p in participants:
            pipe.hsetnx(lb.starts_key(room_id), f"{p['id']}:{q1['id']}", now.isoformat())
        pipe.execute()

    r.publish(lb.state_channel(room_id), json.dumps({
        "type": "go",
        "room_id": room_id,
        "ts": time.time(),
        "start_at": room["start_at"],
        "end_at": room["end_at"],
        "server_now": now.isoformat(),
        "question": {
            "question_id": str(q1["id"]),
            "order_index": q1["order_index"],
            "prompt": q1["prompt"],
            "options": q1["options"],
            "duration_ms": q1["duration_ms"],
            "base_points": q1["base_points"],
            "deadline": (now + timedelta(milliseconds=q1["duration_ms"])).isoformat(),
            "server_now": now.isoformat(),
        },
    }))
    return room


def finalize_room(room_id: str) -> dict | None:
    """
    closed → finalized. Sweeps unanswered questions, persists ranked
    room_results (ON CONFLICT DO NOTHING), broadcasts the final board.
    Returns the finalized room, or None if it was already finalized.
    """
    supa = get_service_supabase()
    supa.rpc("sweep_room", {"p_room": room_id}).execute()

    parts = (
        supa.table("participants")
        .select("id, display_name, score_total, time_total_ms")
        .eq("room_id", room_id)
        .order("score_total", desc=True)
        .order("time_total_ms", desc=False)
        .execute()
        .data
        or []
    )
    if parts:
        supa.table("room_results").upsert(
            [
                {
                    "room_id": room_id,
                    "participant_id": p["id"],
                    "rank": i + 1,
                    "score_total": p["score_total"],
                    "time_total_ms": p["time_total_ms"],
                }
                for i, p in enumerate(parts)
            ],
            on_conflict="room_id,participant_id",
            ignore_duplicates=True,
        ).execute()

    res = (
        supa.table("rooms")
        .update({"status": "finalized", "updated_at": _now_utc().isoformat()})
        .eq("id", room_id)
        .eq("status", "closed")
        .execute()
    )
    if not res.data:
        return None

    get_redis().publish(lb.state_channel(room_id), json.dumps({
        "type": "finalized",
        "room_id": room_id,
        "ts": time.time(),
        "total": len(parts),
        "top": [
            {
                "rank": i + 1,
                "participant_id": p["id"],
                "display_name": p["display_name"],
                "score_total": p["score_total"],
                "time_total_ms": p["time_total_ms"],
            }
            for i, p in enumerate(parts[:FINAL_TOP_K])
        ],
    }))
    lb.mark_room_inactive(room_id)
    return res.data[0]
