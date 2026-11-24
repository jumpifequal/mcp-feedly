"""Access-token management with automatic renewal via a Feedly refresh token.

Feedly developer access tokens expire (roughly monthly). A developer refresh
token can be exchanged for a fresh access token at ``POST /v3/auth/token`` using
the public developer client credentials (``feedlydev`` / ``feedlydev``).

Behaviour:
- The access token is read from ``FEEDLY_ACCESS_TOKEN`` (optional if a refresh
  token is available).
- When a request returns 401 and ``FEEDLY_REFRESH_TOKEN`` is set, the token is
  renewed once and the request retried.
- Renewed tokens are kept in memory only; environment variables are never
  modified and tokens are never logged.
"""

import asyncio
import os
import time
from typing import Optional

import httpx

from .constants import API_BASE_URL, USER_AGENT
from .persist import persist_renewed_tokens, write_log

DEFAULT_CLIENT_ID = "feedlydev"
DEFAULT_CLIENT_SECRET = "feedlydev"


def get_env(name: str) -> Optional[str]:
    """Environment variable, falling back to the Windows *user* environment.

    Some MCP clients (e.g. Claude Desktop) start servers with a reduced
    environment that lacks custom user variables. Reading the HKCU Environment registry key
    directly means tokens never have to be written into client config files.
    """
    value = os.environ.get(name)
    if value:
        return value
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                reg_value = winreg.QueryValueEx(key, name)[0]
            return str(reg_value) or None
        except OSError:
            return None
    return None


class TokenError(Exception):
    """Raised when no usable token exists or renewal fails."""


class TokenManager:
    def __init__(
        self,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        client_id: str = DEFAULT_CLIENT_ID,
        client_secret: str = DEFAULT_CLIENT_SECRET,
        base_url: str = API_BASE_URL,
        timeout: float = 30.0,
    ):
        self._access_token = access_token or None
        self._refresh_token = refresh_token or None
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = base_url
        self._timeout = timeout
        self._lock = asyncio.Lock()
        self.expires_at: Optional[float] = None  # epoch seconds, if known
        self.last_refresh: Optional[float] = None

    @classmethod
    def from_env(cls) -> "TokenManager":
        return cls(
            access_token=get_env("FEEDLY_ACCESS_TOKEN"),
            refresh_token=get_env("FEEDLY_REFRESH_TOKEN"),
            client_id=get_env("FEEDLY_CLIENT_ID") or DEFAULT_CLIENT_ID,
            client_secret=get_env("FEEDLY_CLIENT_SECRET") or DEFAULT_CLIENT_SECRET,
        )

    @property
    def can_refresh(self) -> bool:
        return bool(self._refresh_token)

    async def get_token(self) -> str:
        """Return the current access token, refreshing first if none is set."""
        if self._access_token:
            return self._access_token
        if self.can_refresh:
            return await self.refresh()
        raise TokenError(
            "No Feedly credentials: set FEEDLY_ACCESS_TOKEN and/or FEEDLY_REFRESH_TOKEN."
        )

    async def refresh(self, rejected_token: Optional[str] = None) -> str:
        """Exchange the refresh token for a new access token.

        ``rejected_token`` is the token that just failed; if another task has
        already replaced it, the new token is returned without a second call.
        """
        if not self._refresh_token:
            raise TokenError(
                "Access token rejected and FEEDLY_REFRESH_TOKEN is not set; "
                "cannot renew. Generate a new token at https://feedly.com/v3/auth/dev"
            )
        async with self._lock:
            if (
                rejected_token is not None
                and self._access_token
                and self._access_token != rejected_token
            ):
                return self._access_token

            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/auth/token",
                    json={
                        "refresh_token": self._refresh_token,
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "grant_type": "refresh_token",
                    },
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                )
            if resp.status_code != 200:
                # Never echo the response body verbatim beyond Feedly's message.
                try:
                    msg = resp.json().get("errorMessage", "")
                except Exception:
                    msg = ""
                write_log("TOKEN_REFRESH_FAILED", http_status=resp.status_code, feedly_message=repr(msg))
                raise TokenError(
                    f"Token refresh failed (HTTP {resp.status_code}"
                    f"{': ' + msg if msg else ''}). The refresh token may have "
                    "expired; generate a new one at https://feedly.com/v3/auth/dev"
                )

            data = resp.json()
            new_access = data.get("access_token")
            if not new_access:
                write_log("TOKEN_REFRESH_FAILED", reason="no-access_token-in-response")
                raise TokenError("Token refresh response contained no access_token.")
            self._access_token = new_access
            # Feedly may rotate the refresh token; keep the latest if provided.
            rotated = bool(data.get("refresh_token")) and data["refresh_token"] != self._refresh_token
            if data.get("refresh_token"):
                self._refresh_token = data["refresh_token"]
            expires_in = data.get("expires_in")
            self.expires_at = time.time() + float(expires_in) if expires_in else None
            self.last_refresh = time.time()

            # The renewed token is already good in memory; persisting it to the
            # user environment (and logging) must never fail the request.
            try:
                await persist_renewed_tokens(
                    new_access, self._refresh_token, rotated_refresh=rotated, expires_in=expires_in
                )
            except Exception as e:  # noqa: BLE001
                write_log("TOKEN_PERSIST_ERROR", error=type(e).__name__)
            return new_access
