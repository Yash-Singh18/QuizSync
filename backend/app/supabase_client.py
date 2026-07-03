from functools import lru_cache

from supabase import Client, create_client

from app.config import settings


@lru_cache
def get_supabase() -> Client:
    """Anon client — used only for auth.get_user() JWT validation."""
    return create_client(settings.supabase_url, settings.supabase_key)


@lru_cache
def get_service_supabase() -> Client:
    """Service-role client — bypasses RLS; used for all play/data operations."""
    return create_client(settings.supabase_url, settings.supabase_service_key)
