"""
WebSocket endpoint — live leaderboard downstream.

Auth via query params (role=participant|host + token). Query-param tokens can
land in access logs; a short-lived WS ticket replaces this in Phase 4+.
Each connection gets its own pub/sub subscription to the room's lb channel;
a shared per-room connection manager is a Phase 4 (scale) optimization.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app import leaderboard as lb
from app.redis_client import get_async_redis
from app.supabase_client import get_service_supabase, get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])


def _resolve_participant(code: str, token: str) -> tuple[str, str] | None:
    """Returns (room_id, participant_id) or None if auth fails."""
    supa = get_service_supabase()
    try:
        room = supa.table("rooms").select("id").eq("join_code", code).single().execute().data
        p = (
            supa.table("participants")
            .select("id")
            .eq("room_id", room["id"])
            .eq("session_token", token)
            .single()
            .execute()
            .data
        )
    except Exception:
        return None
    if not room or not p:
        return None
    return room["id"], p["id"]


def _resolve_host(code: str, token: str) -> str | None:
    """Returns room_id if the JWT belongs to the room's host, else None."""
    try:
        response = get_supabase().auth.get_user(token)
        if response is None or response.user is None:
            return None
        host_id = str(response.user.id)
    except Exception:
        return None
    supa = get_service_supabase()
    try:
        room = (
            supa.table("rooms")
            .select("id, host_id")
            .eq("join_code", code)
            .single()
            .execute()
            .data
        )
    except Exception:
        return None
    if not room or room["host_id"] != host_id:
        return None
    return room["id"]


async def _reader(ws: WebSocket) -> None:
    """Client keepalive: reply pong to ping until disconnect."""
    while True:
        msg = await ws.receive_json()
        if msg.get("type") == "ping":
            await ws.send_json({"type": "pong"})


async def _pusher(ws: WebSocket, pubsub, room_id: str, participant_id: str | None) -> None:
    """Forward each broadcaster snapshot, adding the participant's own rank.
    Lifecycle events (room_state / go / finalized) pass through as-is."""
    r = get_async_redis()
    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        snapshot = json.loads(message["data"])

        if snapshot.get("type") != "lb_snapshot":
            await ws.send_json(snapshot)
            continue

        you = None
        if participant_id is not None:
            rank = await r.zrevrank(lb.lb_key(room_id), participant_id)
            score = await r.zscore(lb.lb_key(room_id), participant_id)
            if rank is not None and score is not None:
                points, time_ms = lb.decode(score)
                you = {"rank": rank + 1, "score_total": points, "time_total_ms": time_ms}

        await ws.send_json({
            "type": "lb",
            "ts": snapshot["ts"],
            "total": snapshot["total"],
            "top": snapshot["top"],
            "you": you,
        })


@router.websocket("/ws/rooms/{code}")
async def room_socket(ws: WebSocket, code: str, role: str = "participant", token: str = ""):
    participant_id: str | None = None
    if role == "host":
        room_id = await asyncio.to_thread(_resolve_host, code, token)
    else:
        resolved = await asyncio.to_thread(_resolve_participant, code, token)
        room_id, participant_id = resolved if resolved else (None, None)

    if room_id is None:
        await ws.close(code=4401)  # unauthorized
        return

    await ws.accept()
    pubsub = get_async_redis().pubsub()
    channels = (lb.channel(room_id), lb.state_channel(room_id))
    await pubsub.subscribe(*channels)
    tasks = [
        asyncio.create_task(_reader(ws)),
        asyncio.create_task(_pusher(ws, pubsub, room_id, participant_id)),
    ]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                logger.warning("ws task ended for room %s: %r", room_id, exc)
    finally:
        for task in tasks:
            task.cancel()
        await pubsub.unsubscribe(*channels)
        await pubsub.aclose()
