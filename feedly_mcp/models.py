"""Pydantic v2 input models for Feedly MCP Server."""

from typing import Optional, Literal, List
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum


class ResponseFormat(str, Enum):
    """Output format for tool responses."""

    MARKDOWN = "markdown"
    JSON = "json"


class GetStreamContentsInput(BaseModel):
    """Input for fetching articles from a stream."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    stream_id: str = Field(
        ...,
        description="Stream ID: 'feed/URL' for feeds, 'user/ID/category/label' for categories, 'user/ID/tag/global.saved' for saved articles",
        min_length=1,
    )
    count: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Number of articles to return (1-100, default 20)",
    )
    unread_only: bool = Field(
        default=True,
        description="Only return unread articles (default: True)",
    )
    continuation: Optional[str] = Field(
        default=None,
        description="Continuation token for pagination",
    )
    ranked: Literal["newest", "oldest"] = Field(
        default="newest",
        description="Sort order: 'newest' or 'oldest'",
    )
    newer_than: Optional[int] = Field(
        default=None,
        description="Only return articles newer than this timestamp (epoch ms). Max 31 days in the past.",
    )
    older_than: Optional[int] = Field(
        default=None,
        description="Only return articles older than this timestamp (epoch ms)",
    )
    compact: bool = Field(
        default=False,
        description="Return only title, ID, URL and date per article (saves tokens)",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable or 'json' for machine-readable",
    )


class MarkAsReadInput(BaseModel):
    """Input for marking entries as read."""

    model_config = ConfigDict(extra="forbid")

    entry_ids: List[str] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="List of entry IDs to mark as read (max 1000)",
    )


class MarkFeedAsReadInput(BaseModel):
    """Input for marking an entire feed as read."""

    model_config = ConfigDict(extra="forbid")

    feed_id: str = Field(
        ...,
        description="Feed stream ID (format: 'feed/URL')",
        min_length=1,
    )
    as_of: Optional[int] = Field(
        default=None,
        description="Mark only entries older than this timestamp (epoch ms)",
    )


class MarkCategoryAsReadInput(BaseModel):
    """Input for marking an entire category as read."""

    model_config = ConfigDict(extra="forbid")

    category_id: str = Field(
        ...,
        description="Category stream ID (format: 'user/ID/category/label')",
        min_length=1,
    )
    as_of: Optional[int] = Field(
        default=None,
        description="Mark only entries older than this timestamp (epoch ms)",
    )


class GetEntryInput(BaseModel):
    """Input for getting a single entry."""

    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(
        ...,
        description="Unique entry/article ID",
        min_length=1,
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable or 'json' for machine-readable",
    )


class GetEntriesInput(BaseModel):
    """Input for getting multiple entries."""

    model_config = ConfigDict(extra="forbid")

    entry_ids: List[str] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="List of entry IDs to fetch (max 1000)",
    )
    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable or 'json' for machine-readable",
    )


class KeepUnreadInput(BaseModel):
    """Input for keeping entries unread (undo mark as read)."""

    model_config = ConfigDict(extra="forbid")

    entry_ids: List[str] = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="List of entry IDs to keep unread (max 1000)",
    )


class SimpleResponseFormatInput(BaseModel):
    """Input for tools that only need response format."""

    model_config = ConfigDict(extra="forbid")

    response_format: ResponseFormat = Field(
        default=ResponseFormat.MARKDOWN,
        description="Output format: 'markdown' for human-readable or 'json' for machine-readable",
    )


_RF = Field(
    default=ResponseFormat.MARKDOWN,
    description="Output format: 'markdown' for human-readable or 'json' for machine-readable",
)


class GetSubscriptionsInput(BaseModel):
    """Input for listing subscriptions with paging/filtering."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: Optional[str] = Field(
        default=None, description="Only feeds whose title/website/category contains this text"
    )
    limit: int = Field(default=100, ge=1, le=500, description="Max feeds to return (1-500)")
    offset: int = Field(default=0, ge=0, description="Number of feeds to skip (paging)")
    compact: bool = Field(
        default=False, description="Return only title and feed ID per feed (saves tokens)"
    )
    response_format: ResponseFormat = _RF


class GetFeedInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    feed_id: str = Field(..., min_length=1, description="Feed ID, format 'feed/URL'")
    response_format: ResponseFormat = _RF


class SearchFeedsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(
        ..., min_length=1, description="Keywords, a topic like '#tech', or a site/feed URL"
    )
    count: int = Field(default=20, ge=1, le=100, description="Max results (1-100)")
    locale: Optional[str] = Field(default=None, description="Optional locale, e.g. 'en'")
    response_format: ResponseFormat = _RF


class SearchContentsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    stream_id: str = Field(
        ..., min_length=1, description="Stream to search: feed, category, tag or global.all stream ID"
    )
    query: str = Field(..., min_length=1, description="Search query")
    count: int = Field(default=20, ge=1, le=100, description="Max results (1-100)")
    unread_only: bool = Field(default=False, description="Search unread articles only")
    continuation: Optional[str] = Field(default=None, description="Pagination token")
    newer_than: Optional[int] = Field(default=None, description="Only newer than this epoch-ms timestamp")
    compact: bool = Field(default=False, description="Return only title, ID, URL and date per article")
    response_format: ResponseFormat = _RF


class GetMixInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    stream_id: str = Field(..., min_length=1, description="Stream to rank by engagement")
    count: int = Field(default=10, ge=1, le=50, description="Number of articles (1-50)")
    unread_only: bool = Field(default=True, description="Only unread articles")
    hours: Optional[int] = Field(default=None, ge=1, description="Look-back window in hours")
    compact: bool = Field(default=False, description="Return only title, ID, URL and date per article")
    response_format: ResponseFormat = _RF


class GetRecentReadsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(default=50, ge=1, le=500, description="Max entry IDs (1-500)")
    newer_than: Optional[int] = Field(default=None, description="Only newer than this epoch-ms timestamp")
    response_format: ResponseFormat = _RF


class EntryIdsInput(BaseModel):
    """Generic list of entry IDs (save / unsave)."""

    model_config = ConfigDict(extra="forbid")

    entry_ids: List[str] = Field(..., min_length=1, max_length=1000, description="Entry IDs (max 1000)")


class TagEntriesInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tags: List[str] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Tag labels (e.g. 'Read later') or full IDs 'user/ID/tag/label'. Missing tags are created.",
    )
    entry_ids: List[str] = Field(..., min_length=1, max_length=1000, description="Entry IDs (max 1000)")


class RenameInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(..., min_length=1, description="Full ID or bare label of the tag/category")
    new_label: str = Field(..., min_length=1, description="New label")


class DeleteByIdInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(..., min_length=1, description="Full ID or bare label of the tag/category to delete")


class SubscribeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    feed_id: str = Field(
        ..., min_length=1, description="'feed/URL' (a bare feed URL is also accepted)"
    )
    title: Optional[str] = Field(default=None, description="Custom title")
    categories: Optional[List[str]] = Field(
        default=None,
        description="Category labels or full IDs to file the feed under. Missing categories are created. "
        "When updating an existing subscription this REPLACES its categories.",
    )


class UnsubscribeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    feed_id: str = Field(..., min_length=1, description="Feed ID, format 'feed/URL'")


class ImportOpmlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opml: str = Field(..., min_length=1, description="OPML XML document text to import")
