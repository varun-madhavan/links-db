from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class ItemKind(str, Enum):
    article = "article"
    pdf = "pdf"
    link_only = "link_only"


class FetchStatus(str, Enum):
    ok = "ok"
    partial = "partial"
    failed = "failed"


class ItemStatus(str, Enum):
    inbox = "inbox"
    archived = "archived"
    deleted = "deleted"


class Item(BaseModel):
    id: str
    url: str
    canonical_url: str
    title: str | None = None
    summary: str | None = None
    kind: ItemKind
    fetch_status: FetchStatus
    item_status: ItemStatus = ItemStatus.inbox
    article_path: str | None = None
    pdf_path: str | None = None
    content_text: str | None = None
    read_at: str | None = None
    added_at: str
    updated_at: str
    tags: list[str] = Field(default_factory=list)


class ItemCreate(BaseModel):
    url: str
    tags: list[str] = Field(default_factory=list)


class ItemPatch(BaseModel):
    title: str | None = None
    tags: list[str] | None = None
    item_status: ItemStatus | None = None
    read_at: str | None = None


class ItemListResponse(BaseModel):
    items: list[Item]
    total: int
    limit: int
    offset: int


class SortOption(str, Enum):
    added_at_desc = "added_at_desc"
    added_at_asc = "added_at_asc"
    title_asc = "title_asc"


def row_to_item(row: dict[str, Any], tags: list[str]) -> Item:
    return Item(
        id=row["id"],
        url=row["url"],
        canonical_url=row["canonical_url"],
        title=row["title"],
        summary=row["summary"],
        kind=ItemKind(row["kind"]),
        fetch_status=FetchStatus(row["fetch_status"]),
        item_status=ItemStatus(row["item_status"]),
        article_path=row["article_path"],
        pdf_path=row["pdf_path"],
        content_text=row.get("content_text"),
        read_at=row["read_at"],
        added_at=row["added_at"],
        updated_at=row["updated_at"],
        tags=tags,
    )
