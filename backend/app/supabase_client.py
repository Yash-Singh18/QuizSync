from functools import lru_cache

import httpx
from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions

from app.config import settings

# Force a fresh connection per request. Supabase's edge closes idle keep-alive
# connections, and the cached client would otherwise reuse a dead one →
# httpx.RemoteProtocolError("Server disconnected"). Perf is revisited in Phase 5.
_NO_KEEPALIVE = SyncClientOptions(headers={"Connection": "close"})


class _RetryTransport(httpx.BaseTransport):
    """Retries on RemoteProtocolError — Supabase's edge sporadically closes
    connections mid-handshake even with keep-alive off. Safe to resend: every
    DB call is idempotent by design (ON CONFLICT guards, guarded updates)."""

    def __init__(self, retries: int = 2):
        self._inner = httpx.HTTPTransport()
        self._retries = retries

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(self._retries + 1):
            try:
                return self._inner.handle_request(request)
            except httpx.RemoteProtocolError:
                if attempt == self._retries:
                    raise


def _with_retry(client: Client) -> Client:
    client.postgrest.session._transport = _RetryTransport()
    return client


@lru_cache
def get_supabase() -> Client:
    """Anon client — used only for auth.get_user() JWT validation."""
    return _with_retry(
        create_client(settings.supabase_url, settings.supabase_key, _NO_KEEPALIVE)
    )


@lru_cache
def get_service_supabase() -> Client:
    """Service-role client — bypasses RLS; used for all play/data operations."""
    return _with_retry(
        create_client(settings.supabase_url, settings.supabase_service_key, _NO_KEEPALIVE)
    )
