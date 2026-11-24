"""Async client for Feedly API."""

from typing import Optional, Any
from urllib.parse import quote
import httpx

from .auth import TokenManager, TokenError
from .constants import API_BASE_URL, USER_AGENT


class FeedlyError(Exception):
    """Base exception for Feedly API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _enc(value: str) -> str:
    """URL-encode a path segment (Feedly IDs contain '/', ':' etc.)."""
    return quote(value, safe="")


class FeedlyClient:
    """Async client for Feedly API with automatic token renewal."""

    def __init__(
        self,
        tokens: "TokenManager | str",
        base_url: str = API_BASE_URL,
        timeout: float = 30.0,
    ):
        # Accept a bare access-token string for backwards compatibility.
        self.tokens = tokens if isinstance(tokens, TokenManager) else TokenManager(tokens)
        self.base_url = base_url
        self.timeout = timeout
        self._user_id: Optional[str] = None

    async def _send(
        self,
        token: str,
        method: str,
        endpoint: str,
        params: Optional[dict],
        json_data: Optional[Any],
        content: Optional[str],
        content_type: str,
        accept: str,
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await client.request(
                method,
                f"{self.base_url}{endpoint}",
                params=params,
                json=json_data if content is None else None,
                content=content,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": accept,
                    "Content-Type": content_type,
                    "User-Agent": USER_AGENT,
                },
            )

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        json_data: Optional[Any] = None,
        *,
        content: Optional[str] = None,
        content_type: str = "application/json",
        accept: str = "application/json",
        raw_text: bool = False,
    ) -> Any:
        """Make authenticated request to Feedly API.

        On a 401, renews the access token once (if a refresh token is
        configured) and retries.
        """
        try:
            token = await self.tokens.get_token()
        except TokenError as e:
            raise FeedlyError(str(e), 401)

        response = await self._send(
            token, method, endpoint, params, json_data, content, content_type, accept
        )

        if response.status_code == 401 and self.tokens.can_refresh:
            try:
                token = await self.tokens.refresh(rejected_token=token)
            except TokenError as e:
                raise FeedlyError(str(e), 401)
            response = await self._send(
                token, method, endpoint, params, json_data, content, content_type, accept
            )

        if response.status_code == 401:
            raise FeedlyError(
                "Authentication failed. Check FEEDLY_ACCESS_TOKEN "
                "(and set FEEDLY_REFRESH_TOKEN for automatic renewal).",
                401,
            )
        elif response.status_code == 403:
            raise FeedlyError("Access forbidden. Check your Feedly plan.", 403)
        elif response.status_code == 404:
            raise FeedlyError("Resource not found. Check the ID.", 404)
        elif response.status_code == 429:
            raise FeedlyError("Rate limit exceeded. Wait before retrying.", 429)

        response.raise_for_status()

        # Handle empty responses (204 No Content or empty body with 200)
        if response.status_code == 204 or not response.content:
            return None
        if raw_text:
            return response.text
        return response.json()

    # ------------------------------------------------------------------
    # Account / read
    # ------------------------------------------------------------------

    async def get_profile(self) -> dict:
        """Get user profile information including user ID."""
        profile = await self._request("GET", "/profile")
        if isinstance(profile, dict) and profile.get("id"):
            self._user_id = profile["id"]
        return profile

    async def get_user_id(self) -> str:
        if not self._user_id:
            await self.get_profile()
        if not self._user_id:
            raise FeedlyError("Could not determine Feedly user ID.", None)
        return self._user_id

    async def get_subscriptions(self) -> list[dict]:
        """List all feed subscriptions."""
        return await self._request("GET", "/subscriptions")

    async def get_categories(self) -> list[dict]:
        """List all categories/folders."""
        return await self._request("GET", "/categories")

    async def get_tags(self) -> list[dict]:
        """List all tags."""
        return await self._request("GET", "/tags")

    async def get_unread_counts(self) -> dict:
        """Get unread counts per stream."""
        return await self._request("GET", "/markers/counts")

    async def get_stream_contents(
        self,
        stream_id: str,
        count: int = 20,
        unread_only: bool = True,
        continuation: Optional[str] = None,
        ranked: str = "newest",
        newer_than: Optional[int] = None,
        older_than: Optional[int] = None,
    ) -> dict:
        """Fetch articles from a stream (feed, category, or tag).

        Returns:
            Dict with 'items' list and optional 'continuation' token
        """
        params = {
            "streamId": stream_id,
            "count": count,
            "ranked": ranked,
        }
        if unread_only:
            params["unreadOnly"] = "true"
        if continuation:
            params["continuation"] = continuation
        if newer_than is not None:
            params["newerThan"] = newer_than
        if older_than is not None:
            params["olderThan"] = older_than

        return await self._request("GET", "/streams/contents", params=params)

    async def get_entry(self, entry_id: str) -> list[dict]:
        """Get a single article by its entry ID (API returns an array)."""
        return await self._request("GET", f"/entries/{_enc(entry_id)}")

    async def get_entries(self, entry_ids: list[str]) -> list[dict]:
        """Get multiple articles by their entry IDs (max 1000)."""
        return await self._request("POST", "/entries/.mget", json_data=entry_ids)

    async def get_feed(self, feed_id: str) -> dict:
        """Get metadata for a feed (title, website, subscribers, velocity...)."""
        return await self._request("GET", f"/feeds/{_enc(feed_id)}")

    async def search_feeds(self, query: str, count: int = 20, locale: Optional[str] = None) -> dict:
        """Discover feeds by keyword, topic (#topic) or URL."""
        params: dict[str, Any] = {"query": query, "count": count}
        if locale:
            params["locale"] = locale
        return await self._request("GET", "/search/feeds", params=params)

    async def search_contents(
        self,
        stream_id: str,
        query: str,
        count: int = 20,
        unread_only: bool = False,
        continuation: Optional[str] = None,
        newer_than: Optional[int] = None,
    ) -> dict:
        """Full-text search within a stream (Pro feature)."""
        params: dict[str, Any] = {
            "streamId": stream_id,
            "query": query,
            "count": count,
        }
        if unread_only:
            params["unreadOnly"] = "true"
        if continuation:
            params["continuation"] = continuation
        if newer_than is not None:
            params["newerThan"] = newer_than
        return await self._request("GET", "/search/contents", params=params)

    async def get_mix(
        self,
        stream_id: str,
        count: int = 10,
        unread_only: bool = True,
        hours: Optional[int] = None,
    ) -> dict:
        """Get the most engaging articles of a stream ("must reads")."""
        params: dict[str, Any] = {"streamId": stream_id, "count": count}
        if unread_only:
            params["unreadOnly"] = "true"
        if hours is not None:
            params["hours"] = hours
        return await self._request("GET", "/mixes/contents", params=params)

    async def get_recent_reads(
        self, count: int = 50, newer_than: Optional[int] = None
    ) -> dict:
        """Get entry IDs that were recently marked as read."""
        params: dict[str, Any] = {"count": count}
        if newer_than is not None:
            params["newerThan"] = newer_than
        return await self._request("GET", "/markers/reads", params=params)

    async def export_opml(self) -> str:
        """Export all subscriptions as OPML (XML text)."""
        return await self._request(
            "GET", "/opml", accept="text/xml, application/xml, */*", raw_text=True
        )

    async def import_opml(self, opml_xml: str) -> Any:
        """Import subscriptions from OPML (XML text)."""
        return await self._request(
            "POST", "/opml", content=opml_xml, content_type="text/xml"
        )

    # ------------------------------------------------------------------
    # Read-state markers
    # ------------------------------------------------------------------

    async def mark_as_read(self, entry_ids: list[str]) -> None:
        """Mark entries as read (max 1000)."""
        await self._request(
            "POST",
            "/markers",
            json_data={
                "action": "markAsRead",
                "type": "entries",
                "entryIds": entry_ids,
            },
        )

    async def mark_feed_as_read(
        self, feed_id: str, as_of: Optional[int] = None
    ) -> None:
        """Mark an entire feed as read."""
        data: dict[str, Any] = {
            "action": "markAsRead",
            "type": "feeds",
            "feedIds": [feed_id],
        }
        if as_of is not None:
            data["asOf"] = as_of

        await self._request("POST", "/markers", json_data=data)

    async def mark_category_as_read(
        self, category_id: str, as_of: Optional[int] = None
    ) -> None:
        """Mark an entire category as read."""
        data: dict[str, Any] = {
            "action": "markAsRead",
            "type": "categories",
            "categoryIds": [category_id],
        }
        if as_of is not None:
            data["asOf"] = as_of

        await self._request("POST", "/markers", json_data=data)

    async def keep_unread(self, entry_ids: list[str]) -> None:
        """Keep entries unread (undo mark as read)."""
        await self._request(
            "POST",
            "/markers",
            json_data={
                "action": "keepUnread",
                "type": "entries",
                "entryIds": entry_ids,
            },
        )

    async def mark_as_saved(self, entry_ids: list[str]) -> None:
        """Save entries for later."""
        await self._request(
            "POST",
            "/markers",
            json_data={
                "action": "markAsSaved",
                "type": "entries",
                "entryIds": entry_ids,
            },
        )

    async def mark_as_unsaved(self, entry_ids: list[str]) -> None:
        """Remove entries from saved-for-later."""
        await self._request(
            "POST",
            "/markers",
            json_data={
                "action": "markAsUnsaved",
                "type": "entries",
                "entryIds": entry_ids,
            },
        )

    # ------------------------------------------------------------------
    # Tags / boards
    # ------------------------------------------------------------------

    async def resolve_tag_id(self, tag: str) -> str:
        """Accept a full tag ID or a bare label and return the full tag ID."""
        if tag.startswith("user/"):
            return tag
        return f"user/{await self.get_user_id()}/tag/{tag}"

    async def resolve_category_id(self, category: str) -> str:
        """Accept a full category ID or a bare label and return the full ID."""
        if category.startswith("user/"):
            return category
        return f"user/{await self.get_user_id()}/category/{category}"

    async def tag_entries(self, tag_ids: list[str], entry_ids: list[str]) -> None:
        """Add one or more tags to entries (creates tags that don't exist)."""
        joined = ",".join(_enc(t) for t in tag_ids)
        await self._request(
            "PUT", f"/tags/{joined}", json_data={"entryIds": entry_ids}
        )

    async def untag_entries(self, tag_ids: list[str], entry_ids: list[str]) -> None:
        """Remove tags from entries."""
        t = ",".join(_enc(x) for x in tag_ids)
        e = ",".join(_enc(x) for x in entry_ids)
        await self._request("DELETE", f"/tags/{t}/{e}")

    async def rename_tag(self, tag_id: str, label: str) -> None:
        await self._request("POST", f"/tags/{_enc(tag_id)}", json_data={"label": label})

    async def delete_tags(self, tag_ids: list[str]) -> None:
        joined = ",".join(_enc(t) for t in tag_ids)
        await self._request("DELETE", f"/tags/{joined}")

    # ------------------------------------------------------------------
    # Subscriptions / categories
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        feed_id: str,
        title: Optional[str] = None,
        categories: Optional[list[dict]] = None,
    ) -> Any:
        """Subscribe to a feed (also updates title/categories of an existing one)."""
        data: dict[str, Any] = {"id": feed_id}
        if title:
            data["title"] = title
        if categories is not None:
            data["categories"] = categories
        return await self._request("POST", "/subscriptions", json_data=data)

    async def unsubscribe(self, feed_id: str) -> None:
        await self._request("DELETE", f"/subscriptions/{_enc(feed_id)}")

    async def rename_category(self, category_id: str, label: str) -> None:
        await self._request(
            "POST", f"/categories/{_enc(category_id)}", json_data={"label": label}
        )

    async def delete_category(self, category_id: str) -> None:
        await self._request("DELETE", f"/categories/{_enc(category_id)}")
