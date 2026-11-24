#!/usr/bin/env python3
"""MCP Server for Feedly API integration."""

import argparse
import hmac
import json
import os
import platform
import sys
import time
from pathlib import Path
from datetime import datetime
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from . import __app_name__, __version__
from .auth import TokenManager, get_env
from .client import FeedlyClient, FeedlyError
from .constants import CHARACTER_LIMIT
from .models import (
    DeleteByIdInput,
    EntryIdsInput,
    GetEntriesInput,
    GetEntryInput,
    GetFeedInput,
    GetMixInput,
    GetRecentReadsInput,
    GetStreamContentsInput,
    GetSubscriptionsInput,
    ImportOpmlInput,
    KeepUnreadInput,
    RenameInput,
    SearchContentsInput,
    SearchFeedsInput,
    SubscribeInput,
    TagEntriesInput,
    UnsubscribeInput,
    MarkAsReadInput,
    MarkCategoryAsReadInput,
    MarkFeedAsReadInput,
    ResponseFormat,
    SimpleResponseFormatInput,
)

mcp = FastMCP("feedly_mcp")
# Report OUR version in the MCP initialize handshake (otherwise the SDK's is shown).
mcp._mcp_server.version = __version__

_STARTED = time.time()
_transport = "stdio"
_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "manifest.json"


_client: FeedlyClient | None = None


def get_client() -> FeedlyClient:
    """Get the shared Feedly client.

    A single client (and TokenManager) is reused so a renewed access token
    survives across tool calls.
    """
    global _client
    if _client is None:
        if not (get_env("FEEDLY_ACCESS_TOKEN") or get_env("FEEDLY_REFRESH_TOKEN")):
            raise FeedlyError(
                "Set FEEDLY_ACCESS_TOKEN and/or FEEDLY_REFRESH_TOKEN environment variable", None
            )
        _client = FeedlyClient(TokenManager.from_env())
    return _client


def _handle_error(e: Exception) -> str:
    """Consistent error formatting."""
    if isinstance(e, FeedlyError):
        return f"Error: {e.message}"
    elif isinstance(e, httpx.TimeoutException):
        return "Error: Request timed out. Try again."
    elif isinstance(e, httpx.HTTPStatusError):
        return f"Error: HTTP {e.response.status_code}: {e.response.text}"
    return f"Error: {type(e).__name__}: {str(e)}"


def _truncate_response(content: str) -> str:
    """Truncate response to character limit."""
    if len(content) <= CHARACTER_LIMIT:
        return content
    return content[: CHARACTER_LIMIT - 50] + f"\n\n... [Response truncated at {CHARACTER_LIMIT} characters]"


def _format_timestamp(timestamp_ms: int | None) -> str:
    """Format epoch milliseconds to ISO date string."""
    if timestamp_ms is None:
        return "Unknown"
    try:
        dt = datetime.fromtimestamp(timestamp_ms / 1000)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError):
        return "Unknown"


def _truncate_text(text: str | None, max_length: int = 300) -> str:
    """Truncate text with ellipsis."""
    if text is None:
        return ""
    if len(text) <= max_length:
        return text
    return text[: max_length - 3] + "..."


def _get_article_content(entry: dict) -> str:
    """Extract article content from entry, preferring full content over summary."""
    # Try fullContent first (from Feedly Pro)
    if "fullContent" in entry:
        return entry["fullContent"]
    # Then try content
    if "content" in entry and entry["content"].get("content"):
        return entry["content"]["content"]
    # Fall back to summary
    if "summary" in entry and entry["summary"].get("content"):
        return entry["summary"]["content"]
    return ""


def _format_entry_markdown(entry: dict, include_content: bool = False) -> str:
    """Format a single entry as markdown."""
    title = entry.get("title", "Untitled")
    author = entry.get("author", "Unknown author")
    published = _format_timestamp(entry.get("published"))
    url = entry.get("alternate", [{}])[0].get("href", entry.get("canonicalUrl", ""))
    entry_id = entry.get("id", "")
    unread = entry.get("unread", False)

    content = _get_article_content(entry)
    summary = _truncate_text(content, 300)

    lines = [
        f"### {title}",
        f"**Author:** {author} | **Published:** {published} | **Unread:** {'Yes' if unread else 'No'}",
        f"**ID:** `{entry_id}`",
    ]
    if url:
        lines.append(f"**URL:** [{url}]({url})")

    if include_content and content:
        lines.append(f"\n**Content:**\n{content}")
    elif summary:
        lines.append(f"\n**Summary:** {summary}")

    return "\n".join(lines)


def _format_entries_markdown(entries: list[dict], include_content: bool = False) -> str:
    """Format multiple entries as markdown."""
    if not entries:
        return "No articles found."
    return "\n\n---\n\n".join(
        _format_entry_markdown(entry, include_content) for entry in entries
    )


def _compact_entry(entry: dict) -> dict:
    url = (entry.get("alternate") or [{}])[0].get("href", entry.get("canonicalUrl", ""))
    return {
        "id": entry.get("id", ""),
        "title": entry.get("title", "Untitled"),
        "url": url,
        "published": _format_timestamp(entry.get("published")),
        "source": (entry.get("origin") or {}).get("title", ""),
        "unread": entry.get("unread", False),
    }


def _format_stream_contents(
    data: dict, response_format: ResponseFormat, compact: bool = False
) -> str:
    """Format stream contents response."""
    items = data.get("items") or data.get("results") or []
    continuation = data.get("continuation")

    if compact:
        items = [_compact_entry(i) for i in items]

    if response_format == ResponseFormat.JSON:
        result = {"items": items, "count": len(items)}
        if continuation:
            result["continuation"] = continuation
        return json.dumps(result, indent=2)

    # Markdown format
    lines = [f"## Articles ({len(items)} found)\n"]
    if compact:
        for i in items:
            lines.append(
                f"- **{i['title']}** ({i['source']}, {i['published']}) "
                f"[link]({i['url']}) - ID: `{i['id']}`"
            )
    else:
        lines.append(_format_entries_markdown(items))

    if continuation:
        lines.append(f"\n\n---\n**More articles available.** Use continuation token: `{continuation}`")

    return "\n".join(lines)


def _format_profile_markdown(profile: dict) -> str:
    """Format user profile as markdown."""
    return f"""## Feedly Profile

**User ID:** `{profile.get('id', 'Unknown')}`
**Email:** {profile.get('email', 'Not available')}
**Name:** {profile.get('fullName', profile.get('givenName', 'Not available'))}
**Locale:** {profile.get('locale', 'Not available')}
**Login:** {profile.get('login', 'Not available')}
"""


def _format_subscriptions_markdown(subscriptions: list[dict]) -> str:
    """Format subscriptions list as markdown."""
    if not subscriptions:
        return "No subscriptions found."

    lines = [f"## Subscriptions ({len(subscriptions)} feeds)\n"]
    for sub in subscriptions:
        title = sub.get("title", "Untitled")
        feed_id = sub.get("id", "")
        website = sub.get("website", "")
        categories = [c.get("label", "") for c in sub.get("categories", [])]
        cat_str = ", ".join(categories) if categories else "Uncategorized"

        lines.append(f"### {title}")
        lines.append(f"**Feed ID:** `{feed_id}`")
        if website:
            lines.append(f"**Website:** [{website}]({website})")
        lines.append(f"**Categories:** {cat_str}")
        lines.append("")

    return "\n".join(lines)


def _format_categories_markdown(categories: list[dict]) -> str:
    """Format categories list as markdown."""
    if not categories:
        return "No categories found."

    lines = [f"## Categories ({len(categories)} found)\n"]
    for cat in categories:
        label = cat.get("label", "Unlabeled")
        cat_id = cat.get("id", "")
        lines.append(f"- **{label}**")
        lines.append(f"  - ID: `{cat_id}`")

    return "\n".join(lines)


def _format_tags_markdown(tags: list[dict]) -> str:
    """Format tags list as markdown."""
    if not tags:
        return "No tags found."

    lines = [f"## Tags ({len(tags)} found)\n"]
    for tag in tags:
        label = tag.get("label", tag.get("id", "").split("/")[-1])
        tag_id = tag.get("id", "")
        lines.append(f"- **{label}**")
        lines.append(f"  - ID: `{tag_id}`")

    return "\n".join(lines)


def _format_unread_counts_markdown(data: dict) -> str:
    """Format unread counts as markdown."""
    counts = data.get("unreadcounts", [])
    if not counts:
        return "No unread counts available."

    lines = ["## Unread Counts\n"]
    # Sort by count descending
    sorted_counts = sorted(counts, key=lambda x: x.get("count", 0), reverse=True)

    for item in sorted_counts:
        stream_id = item.get("id", "")
        count = item.get("count", 0)
        updated = _format_timestamp(item.get("updated"))

        # Extract readable name from stream ID
        if "/category/" in stream_id:
            name = stream_id.split("/category/")[-1]
            stream_type = "Category"
        elif "/tag/" in stream_id:
            name = stream_id.split("/tag/")[-1]
            stream_type = "Tag"
        elif stream_id.startswith("feed/"):
            name = stream_id[5:]  # Remove 'feed/' prefix
            stream_type = "Feed"
        else:
            name = stream_id
            stream_type = "Stream"

        lines.append(f"- **{name}** ({stream_type}): {count} unread (updated: {updated})")
        lines.append(f"  - ID: `{stream_id}`")

    return "\n".join(lines)


# =============================================================================
# Tools
# =============================================================================


@mcp.tool(
    name="feedly_get_profile",
    annotations={
        "title": "Get User Profile",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def feedly_get_profile(params: SimpleResponseFormatInput) -> str:
    """Get Feedly user profile information including user ID.

    Use this tool to verify authentication and retrieve the user ID needed
    for constructing stream IDs for categories and tags.

    Args:
        params (SimpleResponseFormatInput): Validated input containing:
            - response_format (str): 'markdown' for human-readable or 'json' for machine-readable

    Returns:
        str: User profile with ID, email, name, and locale.

    Examples:
        - Get profile: response_format="markdown"
        - The user ID is needed for stream IDs like 'user/{userId}/category/Tech'
    """
    try:
        client = get_client()
        profile = await client.get_profile()

        if params.response_format == ResponseFormat.JSON:
            return _truncate_response(json.dumps(profile, indent=2))

        return _truncate_response(_format_profile_markdown(profile))
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="feedly_get_subscriptions",
    annotations={
        "title": "Get Feed Subscriptions",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def feedly_get_subscriptions(params: GetSubscriptionsInput) -> str:
    """List all feed subscriptions.

    Use this tool to discover available feeds and their IDs for fetching articles.

    Args:
        params (SimpleResponseFormatInput): Validated input containing:
            - response_format (str): 'markdown' for human-readable or 'json' for machine-readable

    Returns:
        str: List of subscriptions with feed ID, title, website, and categories.

    Examples:
        - List all subscriptions: response_format="markdown"
        - Feed IDs have format 'feed/https://example.com/rss'
    """
    try:
        client = get_client()
        subscriptions = await client.get_subscriptions()

        if params.query:
            q = params.query.lower()
            subscriptions = [
                s_
                for s_ in subscriptions
                if q in (s_.get("title") or "").lower()
                or q in (s_.get("website") or "").lower()
                or q in (s_.get("id") or "").lower()
                or any(q in (c.get("label") or "").lower() for c in s_.get("categories", []))
            ]
        total = len(subscriptions)
        page = subscriptions[params.offset : params.offset + params.limit]
        if params.compact:
            page = [
                {"id": s_.get("id"), "title": s_.get("title"),
                 "categories": [c.get("label") for c in s_.get("categories", [])]}
                for s_ in page
            ]
        more = params.offset + params.limit < total
        next_offset = params.offset + params.limit if more else None

        if params.response_format == ResponseFormat.JSON:
            return _truncate_response(
                json.dumps(
                    {"total": total, "offset": params.offset, "next_offset": next_offset,
                     "items": page},
                    indent=2,
                )
            )

        if params.compact:
            body = "\n".join(
                f"- **{p_['title']}** - `{p_['id']}` [{', '.join(c for c in p_['categories'] if c)}]"
                for p_ in page
            ) or "No subscriptions found."
            out = f"## Subscriptions ({len(page)} of {total})\n\n{body}"
        else:
            out = _format_subscriptions_markdown(page)
            out = out.replace(f"({len(page)} feeds)", f"({len(page)} of {total} feeds)", 1)
        if more:
            out += f"\n\n---\n**More available.** Call again with offset={next_offset}."
        return _truncate_response(out)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="feedly_get_categories",
    annotations={
        "title": "Get Categories",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def feedly_get_categories(params: SimpleResponseFormatInput) -> str:
    """List all categories/folders.

    Use this tool to discover available categories and their IDs.

    Args:
        params (SimpleResponseFormatInput): Validated input containing:
            - response_format (str): 'markdown' for human-readable or 'json' for machine-readable

    Returns:
        str: List of categories with ID and label.

    Examples:
        - List categories: response_format="markdown"
        - Category IDs have format 'user/{userId}/category/{label}'
    """
    try:
        client = get_client()
        categories = await client.get_categories()

        if params.response_format == ResponseFormat.JSON:
            return _truncate_response(json.dumps(categories, indent=2))

        return _truncate_response(_format_categories_markdown(categories))
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="feedly_get_tags",
    annotations={
        "title": "Get Tags",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def feedly_get_tags(params: SimpleResponseFormatInput) -> str:
    """List all tags including saved articles tag.

    Use this tool to discover available tags and their IDs.

    Args:
        params (SimpleResponseFormatInput): Validated input containing:
            - response_format (str): 'markdown' for human-readable or 'json' for machine-readable

    Returns:
        str: List of tags with ID and label.

    Examples:
        - List tags: response_format="markdown"
        - Saved articles tag: 'user/{userId}/tag/global.saved'
    """
    try:
        client = get_client()
        tags = await client.get_tags()

        if params.response_format == ResponseFormat.JSON:
            return _truncate_response(json.dumps(tags, indent=2))

        return _truncate_response(_format_tags_markdown(tags))
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="feedly_get_unread_counts",
    annotations={
        "title": "Get Unread Counts",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def feedly_get_unread_counts(params: SimpleResponseFormatInput) -> str:
    """Get unread article counts per stream (feed, category, tag).

    Use this tool to check which feeds have unread articles before fetching.

    Args:
        params (SimpleResponseFormatInput): Validated input containing:
            - response_format (str): 'markdown' for human-readable or 'json' for machine-readable

    Returns:
        str: Unread counts for each stream, sorted by count descending.

    Examples:
        - Check unread counts: response_format="markdown"
    """
    try:
        client = get_client()
        counts = await client.get_unread_counts()

        if params.response_format == ResponseFormat.JSON:
            return _truncate_response(json.dumps(counts, indent=2))

        return _truncate_response(_format_unread_counts_markdown(counts))
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="feedly_get_stream_contents",
    annotations={
        "title": "Get Stream Contents",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def feedly_get_stream_contents(params: GetStreamContentsInput) -> str:
    """Fetch articles from a Feedly stream (feed, category, or tag).

    Use this tool to retrieve articles for reading and summarization.
    Supports pagination via continuation token and time-based filtering.

    Args:
        params (GetStreamContentsInput): Validated input containing:
            - stream_id (str): Stream ID ('feed/URL', 'user/ID/category/label', etc.)
            - count (int): Articles to return, 1-100 (default: 20)
            - unread_only (bool): Only unread articles (default: True)
            - continuation (str): Pagination token
            - ranked (str): Sort order 'newest' or 'oldest'
            - newer_than (int): Only articles newer than timestamp (epoch ms, max 31 days)
            - older_than (int): Only articles older than timestamp (epoch ms)
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: Articles with id, title, author, published date, summary, and URL.
             Includes continuation token if more articles available.

    Examples:
        - Get unread from feed: stream_id="feed/https://example.com/rss"
        - Get all articles: stream_id="user/{id}/category/global.all"
        - Get saved articles: stream_id="user/{id}/tag/global.saved"
        - Get articles from last 7 days: newer_than=1704067200000
    """
    try:
        client = get_client()
        data = await client.get_stream_contents(
            stream_id=params.stream_id,
            count=params.count,
            unread_only=params.unread_only,
            continuation=params.continuation,
            ranked=params.ranked,
            newer_than=params.newer_than,
            older_than=params.older_than,
        )
        return _truncate_response(
            _format_stream_contents(data, params.response_format, params.compact)
        )
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="feedly_get_entry",
    annotations={
        "title": "Get Article Details",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def feedly_get_entry(params: GetEntryInput) -> str:
    """Get full details for a single article by its entry ID.

    Use this tool to retrieve the complete content of an article for summarization.

    Args:
        params (GetEntryInput): Validated input containing:
            - entry_id (str): Unique entry/article ID
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: Article with full content, title, author, published date, and URL.

    Examples:
        - Get article: entry_id="..."
    """
    try:
        client = get_client()
        entries = await client.get_entry(params.entry_id)

        if not entries:
            return "Error: Article not found."

        entry = entries[0] if isinstance(entries, list) else entries

        if params.response_format == ResponseFormat.JSON:
            return _truncate_response(json.dumps(entry, indent=2))

        return _truncate_response(_format_entry_markdown(entry, include_content=True))
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="feedly_get_entries",
    annotations={
        "title": "Get Multiple Articles",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def feedly_get_entries(params: GetEntriesInput) -> str:
    """Get full details for multiple articles by their entry IDs.

    Use this tool to batch fetch articles for summarization (max 1000).

    Args:
        params (GetEntriesInput): Validated input containing:
            - entry_ids (list[str]): List of entry IDs (max 1000)
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: Articles with full content, title, author, published date, and URL.

    Examples:
        - Get articles: entry_ids=["id1", "id2", "id3"]
    """
    try:
        client = get_client()
        entries = await client.get_entries(params.entry_ids)

        if params.response_format == ResponseFormat.JSON:
            return _truncate_response(json.dumps(entries, indent=2))

        return _truncate_response(_format_entries_markdown(entries, include_content=True))
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="feedly_mark_as_read",
    annotations={
        "title": "Mark Articles as Read",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def feedly_mark_as_read(params: MarkAsReadInput) -> str:
    """Mark articles as read by their entry IDs.

    Use this after processing articles to track progress.

    Args:
        params (MarkAsReadInput): Validated input containing:
            - entry_ids (list[str]): Entry IDs to mark as read (max 1000)

    Returns:
        str: Confirmation message with count of marked entries.

    Examples:
        - Mark as read: entry_ids=["id1", "id2"]
    """
    try:
        client = get_client()
        await client.mark_as_read(params.entry_ids)
        return f"Successfully marked {len(params.entry_ids)} article(s) as read."
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="feedly_mark_feed_as_read",
    annotations={
        "title": "Mark Feed as Read",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def feedly_mark_feed_as_read(params: MarkFeedAsReadInput) -> str:
    """Mark all articles in a feed as read.

    Use this to mark an entire feed as read at once.

    Args:
        params (MarkFeedAsReadInput): Validated input containing:
            - feed_id (str): Feed stream ID (format: 'feed/URL')
            - as_of (int, optional): Mark only entries older than this timestamp (epoch ms)

    Returns:
        str: Confirmation message.

    Examples:
        - Mark feed as read: feed_id="feed/https://example.com/rss"
        - Mark older entries: feed_id="...", as_of=1704067200000
    """
    try:
        client = get_client()
        await client.mark_feed_as_read(params.feed_id, params.as_of)
        msg = f"Successfully marked feed as read: {params.feed_id}"
        if params.as_of:
            msg += f" (entries before {_format_timestamp(params.as_of)})"
        return msg
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="feedly_mark_category_as_read",
    annotations={
        "title": "Mark Category as Read",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def feedly_mark_category_as_read(params: MarkCategoryAsReadInput) -> str:
    """Mark all articles in a category as read.

    Use this to mark an entire category/folder as read at once.

    Args:
        params (MarkCategoryAsReadInput): Validated input containing:
            - category_id (str): Category stream ID (format: 'user/ID/category/label')
            - as_of (int, optional): Mark only entries older than this timestamp (epoch ms)

    Returns:
        str: Confirmation message.

    Examples:
        - Mark category as read: category_id="user/123/category/Tech"
        - Mark older entries: category_id="...", as_of=1704067200000
    """
    try:
        client = get_client()
        await client.mark_category_as_read(params.category_id, params.as_of)
        msg = f"Successfully marked category as read: {params.category_id}"
        if params.as_of:
            msg += f" (entries before {_format_timestamp(params.as_of)})"
        return msg
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="feedly_keep_unread",
    annotations={
        "title": "Keep Articles Unread",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def feedly_keep_unread(params: KeepUnreadInput) -> str:
    """Keep articles unread (undo mark as read).

    Use this to undo marking articles as read.

    Args:
        params (KeepUnreadInput): Validated input containing:
            - entry_ids (list[str]): Entry IDs to keep unread (max 1000)

    Returns:
        str: Confirmation message with count of entries.

    Examples:
        - Keep unread: entry_ids=["id1", "id2"]
    """
    try:
        client = get_client()
        await client.keep_unread(params.entry_ids)
        return f"Successfully kept {len(params.entry_ids)} article(s) as unread."
    except Exception as e:
        return _handle_error(e)


# =============================================================================
# Additional tools: saved, tags, subscriptions, categories, search, OPML, auth
# =============================================================================


def _ann(title: str, read_only: bool, destructive: bool = False, idempotent: bool = True) -> dict:
    return {
        "title": title,
        "readOnlyHint": read_only,
        "destructiveHint": destructive,
        "idempotentHint": idempotent,
        "openWorldHint": True,
    }


def _json_or_md(data: Any, response_format: ResponseFormat, md: str) -> str:
    if response_format == ResponseFormat.JSON:
        return _truncate_response(json.dumps(data, indent=2))
    return _truncate_response(md)


def _category_ref(cat_id: str) -> dict:
    label = cat_id.split("/category/")[-1] if "/category/" in cat_id else cat_id
    return {"id": cat_id, "label": label}


# ---- Saved for later --------------------------------------------------------


@mcp.tool(name="feedly_save_entries", annotations=_ann("Save Articles for Later", False))
async def feedly_save_entries(params: EntryIdsInput) -> str:
    """Save articles for later (adds them to the 'Saved For Later' list).

    Args:
        params: entry_ids (list[str], max 1000)
    """
    try:
        await get_client().mark_as_saved(params.entry_ids)
        return f"Saved {len(params.entry_ids)} article(s) for later."
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="feedly_unsave_entries", annotations=_ann("Remove Articles from Saved", False))
async def feedly_unsave_entries(params: EntryIdsInput) -> str:
    """Remove articles from 'Saved For Later'.

    Args:
        params: entry_ids (list[str], max 1000)
    """
    try:
        await get_client().mark_as_unsaved(params.entry_ids)
        return f"Removed {len(params.entry_ids)} article(s) from saved."
    except Exception as e:
        return _handle_error(e)


# ---- Tags / boards ----------------------------------------------------------


@mcp.tool(name="feedly_tag_entries", annotations=_ann("Tag Articles", False))
async def feedly_tag_entries(params: TagEntriesInput) -> str:
    """Add one or more tags (boards) to articles. Missing tags are created.

    Tags may be bare labels ('Research') or full IDs ('user/ID/tag/Research').
    """
    try:
        client = get_client()
        tag_ids = [await client.resolve_tag_id(t) for t in params.tags]
        await client.tag_entries(tag_ids, params.entry_ids)
        return f"Tagged {len(params.entry_ids)} article(s) with: {', '.join(tag_ids)}"
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="feedly_untag_entries", annotations=_ann("Remove Tags from Articles", False))
async def feedly_untag_entries(params: TagEntriesInput) -> str:
    """Remove one or more tags from articles (the tags themselves are kept)."""
    try:
        client = get_client()
        tag_ids = [await client.resolve_tag_id(t) for t in params.tags]
        await client.untag_entries(tag_ids, params.entry_ids)
        return f"Removed tag(s) {', '.join(tag_ids)} from {len(params.entry_ids)} article(s)."
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="feedly_rename_tag", annotations=_ann("Rename Tag", False))
async def feedly_rename_tag(params: RenameInput) -> str:
    """Rename a tag/board. `id` may be a bare label or a full tag ID."""
    try:
        client = get_client()
        tag_id = await client.resolve_tag_id(params.id)
        await client.rename_tag(tag_id, params.new_label)
        return f"Renamed tag {tag_id} to '{params.new_label}'."
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="feedly_delete_tag", annotations=_ann("Delete Tag", False, destructive=True))
async def feedly_delete_tag(params: DeleteByIdInput) -> str:
    """Permanently delete a tag/board (articles themselves are not deleted).

    `id` may be a bare label or a full tag ID.
    """
    try:
        client = get_client()
        tag_id = await client.resolve_tag_id(params.id)
        await client.delete_tags([tag_id])
        return f"Deleted tag {tag_id}."
    except Exception as e:
        return _handle_error(e)


# ---- Subscriptions / categories ---------------------------------------------


@mcp.tool(name="feedly_subscribe", annotations=_ann("Subscribe to Feed", False))
async def feedly_subscribe(params: SubscribeInput) -> str:
    """Subscribe to a feed, optionally with a title and categories.

    Missing categories are created. If already subscribed, this updates the
    title and REPLACES the categories (use it to move a feed between folders).
    """
    try:
        client = get_client()
        feed_id = params.feed_id
        if not feed_id.startswith("feed/"):
            feed_id = f"feed/{feed_id}"
        cats = None
        if params.categories is not None:
            cats = [_category_ref(await client.resolve_category_id(c)) for c in params.categories]
        await client.subscribe(feed_id, params.title, cats)
        return f"Subscribed to {feed_id}" + (
            f" in {', '.join(c['label'] for c in cats)}" if cats else ""
        )
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="feedly_unsubscribe", annotations=_ann("Unsubscribe from Feed", False, destructive=True))
async def feedly_unsubscribe(params: UnsubscribeInput) -> str:
    """Unsubscribe from a feed. Removes it from all categories."""
    try:
        await get_client().unsubscribe(params.feed_id)
        return f"Unsubscribed from {params.feed_id}."
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="feedly_rename_category", annotations=_ann("Rename Category", False))
async def feedly_rename_category(params: RenameInput) -> str:
    """Rename a category/folder. `id` may be a bare label or a full category ID."""
    try:
        client = get_client()
        cat_id = await client.resolve_category_id(params.id)
        await client.rename_category(cat_id, params.new_label)
        return f"Renamed category {cat_id} to '{params.new_label}'."
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="feedly_delete_category", annotations=_ann("Delete Category", False, destructive=True))
async def feedly_delete_category(params: DeleteByIdInput) -> str:
    """Delete a category/folder. Feeds in it are kept but become uncategorized."""
    try:
        client = get_client()
        cat_id = await client.resolve_category_id(params.id)
        await client.delete_category(cat_id)
        return f"Deleted category {cat_id}."
    except Exception as e:
        return _handle_error(e)


# ---- Discovery / search -----------------------------------------------------


@mcp.tool(name="feedly_get_feed", annotations=_ann("Get Feed Details", True))
async def feedly_get_feed(params: GetFeedInput) -> str:
    """Get metadata for a feed: title, website, subscriber count, update velocity."""
    try:
        feed = await get_client().get_feed(params.feed_id)
        md = (
            f"## {feed.get('title', 'Untitled')}\n\n"
            f"**ID:** `{feed.get('id', params.feed_id)}`\n"
            f"**Website:** {feed.get('website', 'n/a')}\n"
            f"**Subscribers:** {feed.get('subscribers', 'n/a')}\n"
            f"**Velocity (articles/week):** {feed.get('velocity', 'n/a')}\n"
            f"**Language:** {feed.get('language', 'n/a')}\n"
            f"**Description:** {_truncate_text(feed.get('description'), 500)}"
        )
        return _json_or_md(feed, params.response_format, md)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="feedly_search_feeds", annotations=_ann("Search for Feeds", True))
async def feedly_search_feeds(params: SearchFeedsInput) -> str:
    """Discover feeds by keywords, a '#topic', or a site/feed URL."""
    try:
        data = await get_client().search_feeds(params.query, params.count, params.locale)
        results = data.get("results", []) if isinstance(data, dict) else []
        if not results:
            md = "No feeds found."
        else:
            lines = [f"## Feed results ({len(results)})\n"]
            for r in results:
                lines.append(
                    f"- **{r.get('title', 'Untitled')}** - `{r.get('feedId', r.get('id', ''))}` "
                    f"({r.get('subscribers', '?')} subscribers) {r.get('website', '')}"
                )
            md = "\n".join(lines)
        return _json_or_md(data, params.response_format, md)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="feedly_search_contents", annotations=_ann("Search Articles in a Stream", True))
async def feedly_search_contents(params: SearchContentsInput) -> str:
    """Full-text search within a feed, category, tag or all-articles stream (needs Feedly Pro)."""
    try:
        data = await get_client().search_contents(
            params.stream_id, params.query, params.count, params.unread_only,
            params.continuation, params.newer_than,
        )
        return _truncate_response(
            _format_stream_contents(data or {}, params.response_format, params.compact)
        )
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="feedly_get_most_engaging", annotations=_ann("Get Most Engaging Articles", True))
async def feedly_get_most_engaging(params: GetMixInput) -> str:
    """Get the most engaging ("must read") articles of a stream."""
    try:
        data = await get_client().get_mix(
            params.stream_id, params.count, params.unread_only, params.hours
        )
        return _truncate_response(
            _format_stream_contents(data or {}, params.response_format, params.compact)
        )
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="feedly_get_recent_reads", annotations=_ann("Get Recently Read Entry IDs", True))
async def feedly_get_recent_reads(params: GetRecentReadsInput) -> str:
    """List IDs of entries recently marked as read (useful with feedly_get_entries / feedly_keep_unread)."""
    try:
        data = await get_client().get_recent_reads(params.count, params.newer_than)
        ids = (data.get("entries", []) if isinstance(data, dict) else [])[: params.count]
        if isinstance(data, dict):
            data = {**data, "entries": ids}
        md = f"## Recently read ({len(ids)})\n\n" + "\n".join(f"- `{i}`" for i in ids)
        return _json_or_md(data, params.response_format, md)
    except Exception as e:
        return _handle_error(e)


# ---- OPML -------------------------------------------------------------------


@mcp.tool(name="feedly_export_opml", annotations=_ann("Export Subscriptions as OPML", True))
async def feedly_export_opml(params: SimpleResponseFormatInput) -> str:
    """Export all subscriptions as an OPML XML document."""
    try:
        return _truncate_response(await get_client().export_opml() or "")
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="feedly_import_opml", annotations=_ann("Import Subscriptions from OPML", False, idempotent=False))
async def feedly_import_opml(params: ImportOpmlInput) -> str:
    """Import subscriptions from an OPML XML document. Adds feeds to your account."""
    try:
        await get_client().import_opml(params.opml)
        return "OPML import submitted."
    except Exception as e:
        return _handle_error(e)


# ---- Auth -------------------------------------------------------------------


@mcp.tool(name="feedly_status", annotations=_ann("Server Status and Version", True))
async def feedly_status(params: SimpleResponseFormatInput) -> str:
    """Report the server version, runtime and inventory. No Feedly call, no credentials, no local paths.

    Includes: package version, Python/platform, transport, uptime, tool count,
    whether tokens are configured / auto-renew is available, and (for a
    packaged distribution) the build manifest summary.
    """
    try:
        tools = await mcp.list_tools()
        info: dict[str, Any] = {
            "name": __app_name__,
            "version": __version__,
            "python": platform.python_version(),
            "platform": f"{platform.system().lower()}-{platform.machine().lower()}",
            "transport": _transport,
            "uptime_seconds": int(time.time() - _STARTED),
            "tools": len(tools),
            "read_only_tools": sum(1 for t in tools if (t.annotations and t.annotations.readOnlyHint)),
            "auth": {
                "access_token_configured": bool(get_env("FEEDLY_ACCESS_TOKEN")),
                "refresh_token_configured": bool(get_env("FEEDLY_REFRESH_TOKEN")),
                "auto_renew_available": bool(get_env("FEEDLY_REFRESH_TOKEN")),
            },
        }
        try:
            m = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
            info["distribution"] = {
                "built": m.get("built_at"),
                "target": m.get("target"),
                "manifest_version": m.get("manifest_version"),
            }
        except Exception:
            info["distribution"] = None  # running from a source checkout
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(info, indent=2)
        lines = [f"## {info['name']} {info['version']}", ""]
        for k in ("python", "platform", "transport", "uptime_seconds", "tools", "read_only_tools"):
            lines.append(f"- **{k}:** {info[k]}")
        a = info["auth"]
        lines.append(f"- **auth:** access token {'set' if a['access_token_configured'] else 'missing'}, "
                     f"auto-renew {'available' if a['auto_renew_available'] else 'unavailable'}")
        if info["distribution"]:
            lines.append(f"- **distribution:** built {info['distribution']['built']} for {info['distribution']['target']}")
        return "\n".join(lines)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="feedly_auth_status", annotations=_ann("Check Feedly Authentication", True))
async def feedly_auth_status(params: SimpleResponseFormatInput) -> str:
    """Report whether the access token works and whether auto-renewal is available.

    Never reveals token values.
    """
    try:
        client = get_client()
        tm = client.tokens
        info = {
            "has_access_token": bool(getattr(tm, "_access_token", None)),
            "auto_renew_available": tm.can_refresh,
            "renewed_this_session": tm.last_refresh is not None,
        }
        try:
            await client.get_profile()
            info["token_valid"] = True
        except FeedlyError as e:
            info["token_valid"] = False
            info["error"] = e.message
        return json.dumps(info, indent=2)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(name="feedly_refresh_access_token", annotations=_ann("Renew Access Token", False))
async def feedly_refresh_access_token(params: SimpleResponseFormatInput) -> str:
    """Force-renew the access token using FEEDLY_REFRESH_TOKEN.

    Normally unnecessary: renewal happens automatically on a 401. The new
    token is kept in memory only and never shown.
    """
    try:
        await get_client().tokens.refresh()
        return "Access token renewed (kept in memory for this server session)."
    except Exception as e:
        return _handle_error(e)


# =============================================================================
# Entry point: stdio (default) or streamable HTTP protected by a bearer token
# =============================================================================


class BearerAuthMiddleware:
    """Pure-ASGI middleware requiring 'Authorization: Bearer <MCP_BEARER_TOKEN>'."""

    def __init__(self, app, token: str):
        self.app = app
        self._token = token.encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            supplied = b""
            for name, value in scope.get("headers", []):
                if name == b"authorization":
                    if value[:7].lower() == b"bearer ":
                        supplied = value[7:].strip()
                    break
            if not hmac.compare_digest(supplied, self._token):
                body = b'{"error":"unauthorized"}'
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                            (b"www-authenticate", b'Bearer realm="feedly-mcp"'),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def build_http_app(token: str):
    """Streamable-HTTP ASGI app (endpoint: /mcp) wrapped with bearer auth."""
    return BearerAuthMiddleware(mcp.streamable_http_app(), token)


def main(argv: list[str] | None = None):
    """Entry point for the MCP server."""
    parser = argparse.ArgumentParser(prog="feedly_mcp_server.py", description="Feedly MCP server")
    parser.add_argument("--version", action="version", version=f"{__app_name__} {__version__}")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=os.environ.get("MCP_TRANSPORT", "stdio"),
        help="stdio (default) or http (streamable HTTP at /mcp, bearer-protected)",
    )
    parser.add_argument("--host", default=os.environ.get("MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MCP_PORT", "8000")))
    args = parser.parse_args(argv)

    global _transport
    _transport = "streamable-http" if args.transport == "http" else "stdio"

    if args.transport == "stdio":
        mcp.run()
        return

    token = os.environ.get("MCP_BEARER_TOKEN", "")
    if len(token) < 16:
        sys.exit(
            "MCP_BEARER_TOKEN must be set (min 16 chars) to run the HTTP transport. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    import uvicorn

    # Bearer auth protects the endpoint, so don't restrict the Host header when
    # binding beyond loopback (the SDK only auto-allows localhost hosts).
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        mcp.settings.transport_security = None
    print(f"Feedly MCP over HTTP on http://{args.host}:{args.port}/mcp (bearer required)", file=sys.stderr)
    uvicorn.run(build_http_app(token), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
