"""
Snapshot broadcaster — in-process asyncio task (Phase 2; extracted to the
Background Worker in Phase 3).

Every tick it publishes ONE top-K snapshot per active room through Redis
pub/sub, so fan-out volume is independent of submission rate and the path
is already backplane-ready for multi-instance (Phase 4).
"""

import asyncio
import json
import logging
import time

from app import leaderboard as lb
from app.redis_client import get_async_redis
from app.supabase_client import get_service_supabase

logger = logging.getLogger(__name__)

TICK_SECONDS = 1.5
TOP_K = 20
RECONCILE_EVERY = 10  # ticks (~15s) — heals rooms:active after a Redis flush


def _live_room_ids() -> list[str]:
    supa = get_service_supabase()
    res = supa.table("rooms").select("id").eq("status", "live").execute()
    return [r["id"] for r in (res.data or [])]


async def _snapshot_room(r, room_id: str) -> None:
    if await r.zcard(lb.lb_key(room_id)) == 0:
        # ZSET missing (Redis flush / restart) — heal from Postgres
        await asyncio.to_thread(lb.rebuild_room_zset, room_id)

    total = await r.zcard(lb.lb_key(room_id))
    top = await r.zrevrange(lb.lb_key(room_id), 0, TOP_K - 1, withscores=True)
    pids = [pid for pid, _ in top]
    names = await r.hmget(lb.names_key(room_id), pids) if pids else []

    rows = []
    for rank, ((pid, score), name) in enumerate(zip(top, names), start=1):
        points, time_ms = lb.decode(score)
        rows.append({
            "rank": rank,
            "participant_id": pid,
            "display_name": name or "?",
            "score_total": points,
            "time_total_ms": time_ms,
        })

    await r.publish(lb.channel(room_id), json.dumps({
        "type": "lb_snapshot",
        "room_id": room_id,
        "ts": time.time(),
        "total": total,
        "top": rows,
    }))


async def snapshot_loop(stop_event: asyncio.Event) -> None:
    r = get_async_redis()
    tick = 0
    while not stop_event.is_set():
        try:
            if tick % RECONCILE_EVERY == 0:
                live_ids = await asyncio.to_thread(_live_room_ids)
                if live_ids:
                    await r.sadd(lb.ACTIVE_ROOMS, *live_ids)

            for room_id in await r.smembers(lb.ACTIVE_ROOMS):
                try:
                    await _snapshot_room(r, room_id)
                except Exception:
                    logger.exception("snapshot failed for room %s", room_id)
        except Exception:
            logger.exception("broadcaster tick failed")

        tick += 1
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=TICK_SECONDS)
        except TimeoutError:
            pass
