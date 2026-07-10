"""
Background worker — scheduler + snapshot broadcaster (Phase 3).

Separate deployable from the web service; run exactly ONE instance (the
singleton). Leader election for >1 instance is a Phase 4 concern.

    python -m app.worker
"""

import asyncio
import logging
import signal

from app.broadcaster import snapshot_loop
from app.redis_client import close_redis
from app.scheduler import scheduler_loop


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    logging.getLogger(__name__).info("worker started")
    await asyncio.gather(snapshot_loop(stop_event), scheduler_loop(stop_event))
    await close_redis()


if __name__ == "__main__":
    asyncio.run(main())
