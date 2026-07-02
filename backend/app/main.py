from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.supabase_client import get_supabase

app = FastAPI(title="QuizSync API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    supabase_ok = False
    if settings.supabase_url and settings.supabase_key:
        try:
            get_supabase().table("healthcheck").select("id").limit(1).execute()
            supabase_ok = True
        except Exception:
            supabase_ok = False
    return {"status": "ok", "supabase": supabase_ok}
