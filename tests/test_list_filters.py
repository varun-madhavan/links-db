from __future__ import annotations

from pathlib import Path

from links_db import db
from links_db.models import FetchStatus, ItemKind, ItemStatus, SortOption, utc_now_iso


def _insert(
    conn,
    *,
    item_id: str,
    url: str,
    canonical_url: str,
    title: str | None,
    tags: list[str],
    read_at: str | None = None,
    item_status: ItemStatus = ItemStatus.inbox,
) -> None:
    now = utc_now_iso()
    db.insert_item(
        conn,
        item_id=item_id,
        url=url,
        canonical_url=canonical_url,
        title=title,
        summary=None,
        kind=ItemKind.link_only,
        fetch_status=FetchStatus.ok,
        item_status=item_status,
        article_path=None,
        pdf_path=None,
        content_text=None,
        read_at=read_at,
        now=now,
        tags=tags,
    )


def test_list_items_q_matches_title_or_url(tmp_path: Path) -> None:
    data = tmp_path
    db.ensure_data_dirs(data)
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    _insert(
        conn,
        item_id="a",
        url="https://ex.com/foo",
        canonical_url="https://ex.com/foo",
        title="Alpha Article",
        tags=["x"],
    )
    _insert(
        conn,
        item_id="b",
        url="https://unique-path.example/bar",
        canonical_url="https://unique-path.example/bar",
        title="Other",
        tags=["y"],
    )
    items, total = db.list_items(conn, q="alpha", item_status=ItemStatus.inbox)
    assert total == 1
    assert items[0].id == "a"

    items2, total2 = db.list_items(conn, q="unique-path", item_status=ItemStatus.inbox)
    assert total2 == 1
    assert items2[0].id == "b"


def test_list_items_multi_tag_or(tmp_path: Path) -> None:
    data = tmp_path
    db.ensure_data_dirs(data)
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    _insert(
        conn,
        item_id="a",
        url="https://a.example/",
        canonical_url="https://a.example/",
        title="A",
        tags=["foo"],
    )
    _insert(
        conn,
        item_id="b",
        url="https://b.example/",
        canonical_url="https://b.example/",
        title="B",
        tags=["bar"],
    )
    items, total = db.list_items(conn, tags=["foo", "bar"], item_status=ItemStatus.inbox)
    assert total == 2
    assert {it.id for it in items} == {"a", "b"}

    items_one, total_one = db.list_items(conn, tags=["foo"], item_status=ItemStatus.inbox)
    assert total_one == 1
    assert items_one[0].id == "a"


def test_list_items_tag_singular_merged_with_tags(tmp_path: Path) -> None:
    data = tmp_path
    db.ensure_data_dirs(data)
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    _insert(
        conn,
        item_id="a",
        url="https://a.example/",
        canonical_url="https://a.example/",
        title="A",
        tags=["foo"],
    )
    _insert(
        conn,
        item_id="b",
        url="https://b.example/",
        canonical_url="https://b.example/",
        title="B",
        tags=["bar"],
    )
    items, total = db.list_items(conn, tag="foo", tags=["bar"], item_status=ItemStatus.inbox)
    assert total == 2


def test_list_items_require_read_at(tmp_path: Path) -> None:
    data = tmp_path
    db.ensure_data_dirs(data)
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    now = utc_now_iso()
    _insert(
        conn,
        item_id="r",
        url="https://r.example/",
        canonical_url="https://r.example/",
        title="R",
        tags=[],
        read_at=now,
    )
    _insert(
        conn,
        item_id="u",
        url="https://u.example/",
        canonical_url="https://u.example/",
        title="U",
        tags=[],
        read_at=None,
    )
    read_items, tr = db.list_items(
        conn, item_status=ItemStatus.inbox, require_read_at=True
    )
    assert tr == 1
    assert read_items[0].id == "r"

    unread, tu = db.list_items(
        conn, item_status=ItemStatus.inbox, require_read_at=False
    )
    assert tu == 1
    assert unread[0].id == "u"


def test_list_tag_names_prefix(tmp_path: Path) -> None:
    data = tmp_path
    db.ensure_data_dirs(data)
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    _insert(
        conn,
        item_id="a",
        url="https://a.example/",
        canonical_url="https://a.example/",
        title="A",
        tags=["alpha", "beta"],
    )
    names = db.list_tag_names(conn, q="al", limit=20)
    assert "alpha" in names
    assert "beta" not in names

    inbox_names = db.list_tag_names(
        conn, q="be", limit=20, item_status=ItemStatus.inbox
    )
    assert inbox_names == ["beta"]
