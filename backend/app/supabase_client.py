from functools import lru_cache

from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions

from app.config import settings

# Force a fresh connection per request. Supabase's edge closes idle keep-alive
# connections, and the cached client would otherwise reuse a dead one →
# httpx.RemoteProtocolError("Server disconnected"). Perf is revisited in Phase 5.
_NO_KEEPALIVE = SyncClientOptions(headers={"Connection": "close"})


@lru_cache
def get_supabase() -> Client:
    """Anon client — used only for auth.get_user() JWT validation."""
    return create_client(settings.supabase_url, settings.supabase_key, _NO_KEEPALIVE)


@lru_cache
def get_service_supabase() -> Client:
    """Service-role client — bypasses RLS; used for all play/data operations."""
    return create_client(
        settings.supabase_url, settings.supabase_service_key, _NO_KEEPALIVE
    )
