"""
Scheduler — time-based room transitions (runs inside the background worker).

Every ~1s: scheduled → live at T0 (go_live broadcasts `go` + Q1),
live → closed at T_end, closed → finalized. Also publishes a lobby
countdown tick for each still-scheduled room. All transitions are guarded
UPDATEs, so a missed tick or a restarted worker just re-runs them safely.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from app import leaderboard as lb
from app.lifecycle import finalize_room, go_live
from app.redis_client import get_async_redis
from app.supabase_client import get_service_supabase

logger = logging.getLogger(__name__)

POLL_SECONDS = 1.0


def _parse_ts(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _scheduled_rooms() -> list[dict]:
    supa = get_service_supabase()
    res = supa.table("rooms").select("id, start_at").eq("status", "scheduled").execute()
    return res.data or []


def _close_due_rooms(now_iso: str) -> list[str]:
    """Guarded bulk close: live rooms past T_end. Returns the closed ids."""
    supa = get_service_supabase()
    res = (
        supa.table("rooms")
        .update({"status": "closed", "updated_at": now_iso})
        .eq("status", "live")
        .lte("end_at", now_iso)
        .execute()
    )
    return [room["id"] for room in (res.data or [])]


def _closed_room_ids() -> list[str]:
    supa = get_service_supabase()
    res = supa.table("rooms").select("id").eq("status", "closed").execute()
    return [room["id"] for room in (res.data or [])]


async def _tick(r) -> None:
    now = datetime.now(tz=timezone.utc)

    for room in await asyncio.to_thread(_scheduled_rooms):
        try:
            if not room["start_at"]:
                continue
            start_at = _parse_ts(room["start_at"])
            if start_at <= now:
                if await asyncio.to_thread(go_live, room["id"]):
                    logger.info("room %s went live at T0", room["id"])
            else:
                await r.publish(lb.state_channel(room["id"]), json.dumps({
                    "type": "room_state",
                    "room_id": room["id"],
                    "status": "scheduled",
                    "start_at": room["start_at"],
                    "seconds_to_start": int((start_at - now).total_seconds()),
                    "server_now": now.isoformat(),
                    "ts": time.time(),
                }))
        except Exception:
            logger.exception("scheduled-room tick failed for %s", room["id"])

    for room_id in await asyncio.to_thread(_close_due_rooms, now.isoformat()):
        logger.info("room %s closed at T_end", room_id)

    for room_id in await asyncio.to_thread(_closed_room_ids):
        try:
            if await asyncio.to_thread(finalize_room, room_id):
                logger.info("room %s finalized", room_id)
        except Exception:
            logger.exception("finalize failed for room %s", room_id)


async def scheduler_loop(stop_event: asyncio.Event) -> None:
    r = get_async_redis()
    while not stop_event.is_set():
        try:
            await _tick(r)
        except Exception:
            logger.exception("scheduler tick failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLL_SECONDS)
        except TimeoutError:
            pass
