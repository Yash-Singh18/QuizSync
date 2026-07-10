from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.play import router as play_router
from app.redis_client import close_redis, get_redis
from app.rooms import router as rooms_router
from app.supabase_client import get_supabase
from app.ws import router as ws_router

# The snapshot broadcaster + scheduler run in the background worker
# (app.worker) since Phase 3 — the web process only serves HTTP + WS.


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await close_redis()


app = FastAPI(title="QuizSync API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rooms_router)
app.include_router(play_router)
app.include_router(ws_router)


@app.get("/health")
def health():
    supabase_ok = False
    if settings.supabase_url and settings.supabase_key:
        try:
            get_supabase().table("healthcheck").select("id").limit(1).execute()
            supabase_ok = True
        except Exception:
            supabase_ok = False
    redis_ok = False
    try:
        redis_ok = bool(get_redis().ping())
    except Exception:
        redis_ok = False
    return {"status": "ok", "supabase": supabase_ok, "redis": redis_ok}
