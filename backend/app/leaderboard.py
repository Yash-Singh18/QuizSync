"""
Leaderboard helpers — Redis key scheme, composite score encoding, and the
ZSET maintenance/rebuild routines. Postgres is truth; the ZSET is a cache
that is always rebuildable from participant aggregates.
"""

import math

from app.redis_client import get_redis
from app.supabase_client import get_service_supabase

ACTIVE_ROOMS = "rooms:active"
KEY_TTL_SECONDS = 3600  # applied on room end; results page reads Postgres


# ── keys & channels ───────────────────────────────────────────────────────

def lb_key(room_id: str) -> str:
    return f"room:{room_id}:lb"


def starts_key(room_id: str) -> str:
    return f"room:{room_id}:starts"


def names_key(room_id: str) -> str:
    return f"room:{room_id}:names"


def channel(room_id: str) -> str:
    return f"room:{room_id}:lb"


def state_channel(room_id: str) -> str:
    """Lifecycle events: room_state (lobby countdown), go (T0 + Q1), finalized."""
    return f"room:{room_id}:state"


# ── composite score (SystemDesign §3.2) ──────────────────────────────────

def composite(points_total: int, time_total_ms: int) -> float:
    # fractional term < 1 always (time under ~11.5 days), so points dominate;
    # more time -> smaller composite -> lower rank.
    return points_total - (time_total_ms / 1e9)


def decode(score: float) -> tuple[int, int]:
    points = math.ceil(score - 1e-12)
    time_ms = round((points - score) * 1e9)
    return points, time_ms


# ── ZSET maintenance ─────────────────────────────────────────────────────

def update_participant_score(room_id: str, participant_id: str) -> None:
    """Re-read aggregates from Postgres (truth) and ZADD. Called after the
    record_answer transaction commits; idempotent."""
    supa = get_service_supabase()
    res = (
        supa.table("participants")
        .select("score_total, time_total_ms")
        .eq("id", participant_id)
        .maybe_single()
        .execute()
    )
    if res is None or not res.data:
        return
    get_redis().zadd(
        lb_key(room_id),
        {participant_id: composite(res.data["score_total"], res.data["time_total_ms"])},
    )


def rebuild_room_zset(room_id: str) -> None:
    """Heal routine: reconstruct the ZSET + names hash from Postgres."""
    supa = get_service_supabase()
    res = (
        supa.table("participants")
        .select("id, display_name, score_total, time_total_ms")
        .eq("room_id", room_id)
        .execute()
    )
    rows = res.data or []
    if not rows:
        return
    pipe = get_redis().pipeline()
    pipe.zadd(
        lb_key(room_id),
        {p["id"]: composite(p["score_total"], p["time_total_ms"]) for p in rows},
    )
    pipe.hset(names_key(room_id), mapping={p["id"]: p["display_name"] for p in rows})
    pipe.execute()


def mark_room_active(room_id: str) -> None:
    pipe = get_redis().pipeline()
    pipe.sadd(ACTIVE_ROOMS, room_id)
    # Clear any leftover TTLs from a previous end (e.g. restarted contest)
    for key in (lb_key(room_id), starts_key(room_id), names_key(room_id)):
        pipe.persist(key)
    pipe.execute()


def mark_room_inactive(room_id: str) -> None:
    pipe = get_redis().pipeline()
    pipe.srem(ACTIVE_ROOMS, room_id)
    for key in (lb_key(room_id), starts_key(room_id), names_key(room_id)):
        pipe.expire(key, KEY_TTL_SECONDS)
    pipe.execute()
