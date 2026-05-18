from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from links_db.models import (
    FetchStatus,
    Item,
    ItemKind,
    ItemStatus,
    SortOption,
    row_to_item,
    utc_now_iso,
)

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS items (
  id             TEXT PRIMARY KEY,
  url            TEXT NOT NULL,
  canonical_url  TEXT NOT NULL UNIQUE,
  title          TEXT,
  summary        TEXT,
  kind           TEXT NOT NULL,
  fetch_status   TEXT NOT NULL,
  item_status    TEXT NOT NULL DEFAULT 'inbox',
  article_path   TEXT,
  pdf_path       TEXT,
  content_text   TEXT,
  read_at        TEXT,
  added_at       TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS item_tags (
  item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (item_id, tag_id)
);

CREATE INDEX IF NOT EXISTS idx_items_canonical_url ON items(canonical_url);
CREATE INDEX IF NOT EXISTS idx_items_item_status ON items(item_status);
CREATE INDEX IF NOT EXISTS idx_items_added_at ON items(added_at);
CREATE INDEX IF NOT EXISTS idx_item_tags_tag_id ON item_tags(tag_id);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def normalize_tag(name: str) -> str:
    return name.strip().lower()


def ensure_data_dirs(data_dir: Path) -> None:
    (data_dir / "articles").mkdir(parents=True, exist_ok=True)
    (data_dir / "pdfs").mkdir(parents=True, exist_ok=True)


def get_item_by_id(conn: sqlite3.Connection, item_id: str) -> Item | None:
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        return None
    return row_to_item(dict(row), _tags_for_item(conn, item_id))


def get_item_by_canonical_url(conn: sqlite3.Connection, canonical_url: str) -> Item | None:
    row = conn.execute(
        "SELECT * FROM items WHERE canonical_url = ?", (canonical_url,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    return row_to_item(d, _tags_for_item(conn, d["id"]))


def _tags_for_item(conn: sqlite3.Connection, item_id: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT t.name FROM tags t
        JOIN item_tags it ON it.tag_id = t.id
        WHERE it.item_id = ?
        ORDER BY t.name
        """,
        (item_id,),
    ).fetchall()
    return [r[0] for r in rows]


def _get_or_create_tag_ids(conn: sqlite3.Connection, tag_names: list[str]) -> list[int]:
    ids: list[int] = []
    for raw in tag_names:
        name = normalize_tag(raw)
        if not name:
            continue
        row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        if row:
            ids.append(int(row[0]))
        else:
            cur = conn.execute("INSERT INTO tags (name) VALUES (?)", (name,))
            ids.append(int(cur.lastrowid))
    return ids


def set_item_tags_replace(conn: sqlite3.Connection, item_id: str, tag_names: list[str]) -> None:
    conn.execute("DELETE FROM item_tags WHERE item_id = ?", (item_id,))
    for tid in _get_or_create_tag_ids(conn, tag_names):
        conn.execute(
            "INSERT OR IGNORE INTO item_tags (item_id, tag_id) VALUES (?, ?)",
            (item_id, tid),
        )


def union_item_tags(conn: sqlite3.Connection, item_id: str, tag_names: list[str]) -> None:
    for tid in _get_or_create_tag_ids(conn, tag_names):
        conn.execute(
            "INSERT OR IGNORE INTO item_tags (item_id, tag_id) VALUES (?, ?)",
            (item_id, tid),
        )


def insert_item(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    url: str,
    canonical_url: str,
    title: str | None,
    summary: str | None,
    kind: ItemKind,
    fetch_status: FetchStatus,
    item_status: ItemStatus,
    article_path: str | None,
    pdf_path: str | None,
    content_text: str | None,
    read_at: str | None,
    now: str,
    tags: list[str],
) -> Item:
    conn.execute(
        """
        INSERT INTO items (
          id, url, canonical_url, title, summary, kind, fetch_status,
          item_status, article_path, pdf_path, content_text, read_at, added_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            url,
            canonical_url,
            title,
            summary,
            kind.value,
            fetch_status.value,
            item_status.value,
            article_path,
            pdf_path,
            content_text,
            read_at,
            now,
            now,
        ),
    )
    union_item_tags(conn, item_id, tags)
    conn.commit()
    item = get_item_by_id(conn, item_id)
    assert item is not None
    return item


def update_item_row(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    url: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    kind: ItemKind | None = None,
    fetch_status: FetchStatus | None = None,
    article_path: str | None = None,
    pdf_path: str | None = None,
    content_text: str | None = None,
    item_status: ItemStatus | None = None,
    read_at: str | None = None,
    clear_article: bool = False,
    clear_pdf: bool = False,
    clear_content_text: bool = False,
) -> None:
    fields: list[str] = []
    values: list[Any] = []
    if url is not None:
        fields.append("url = ?")
        values.append(url)
    if title is not None:
        fields.append("title = ?")
        values.append(title)
    if summary is not None:
        fields.append("summary = ?")
        values.append(summary)
    if kind is not None:
        fields.append("kind = ?")
        values.append(kind.value)
    if fetch_status is not None:
        fields.append("fetch_status = ?")
        values.append(fetch_status.value)
    if article_path is not None:
        fields.append("article_path = ?")
        values.append(article_path)
    if pdf_path is not None:
        fields.append("pdf_path = ?")
        values.append(pdf_path)
    if content_text is not None:
        fields.append("content_text = ?")
        values.append(content_text)
    if clear_article:
        fields.append("article_path = NULL")
    if clear_pdf:
        fields.append("pdf_path = NULL")
    if clear_content_text:
        fields.append("content_text = NULL")
    if item_status is not None:
        fields.append("item_status = ?")
        values.append(item_status.value)
    if read_at is not None:
        fields.append("read_at = ?")
        values.append(read_at)
    now = utc_now_iso()
    fields.append("updated_at = ?")
    values.append(now)
    values.append(item_id)
    if not fields:
        return
    sql = f"UPDATE items SET {', '.join(fields)} WHERE id = ?"
    conn.execute(sql, values)


def _dedupe_normalized_tags(tag: str | None, tags: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in (*(tags or []), *([tag] if tag else [])):
        n = normalize_tag(raw)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def list_items(
    conn: sqlite3.Connection,
    *,
    tag: str | None = None,
    tags: list[str] | None = None,
    q: str | None = None,
    require_read_at: bool | None = None,
    kind: ItemKind | None = None,
    fetch_status: FetchStatus | None = None,
    item_status: ItemStatus = ItemStatus.inbox,
    limit: int = 50,
    offset: int = 0,
    sort: SortOption = SortOption.added_at_desc,
) -> tuple[list[Item], int]:
    where: list[str] = ["1=1"]
    params: list[Any] = []

    where.append("i.item_status = ?")
    params.append(item_status.value)
    if kind is not None:
        where.append("i.kind = ?")
        params.append(kind.value)
    if fetch_status is not None:
        where.append("i.fetch_status = ?")
        params.append(fetch_status.value)

    if require_read_at is True:
        where.append("i.read_at IS NOT NULL")
    elif require_read_at is False:
        where.append("i.read_at IS NULL")

    qn = (q or "").strip()
    if qn:
        where.append(
            "(instr(lower(coalesce(i.title, '')), lower(?)) > 0 "
            "OR instr(lower(i.url), lower(?)) > 0)"
        )
        params.extend([qn, qn])

    tag_names = _dedupe_normalized_tags(tag, tags)
    if tag_names:
        placeholders = ",".join("?" * len(tag_names))
        where.append(
            f"""EXISTS (
            SELECT 1 FROM item_tags it2
            JOIN tags t2 ON t2.id = it2.tag_id
            WHERE it2.item_id = i.id AND t2.name IN ({placeholders})
        )"""
        )
        params.extend(tag_names)

    where_sql = " AND ".join(where)
    order = {
        SortOption.added_at_desc: "i.added_at DESC",
        SortOption.added_at_asc: "i.added_at ASC",
        SortOption.title_asc: "i.title COLLATE NOCASE ASC NULLS LAST, i.added_at DESC",
    }[sort]

    count_sql = f"""
        SELECT COUNT(*) FROM items i
        WHERE {where_sql}
    """
    total = int(conn.execute(count_sql, params).fetchone()[0])

    list_sql = f"""
        SELECT i.id FROM items i
        WHERE {where_sql}
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """
    list_params = [*params, limit, offset]
    id_rows = conn.execute(list_sql, list_params).fetchall()
    ids = [r[0] for r in id_rows]
    if not ids:
        return [], total

    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT * FROM items WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    row_by_id = {dict(r)["id"]: dict(r) for r in rows}
    items = [row_to_item(row_by_id[i], _tags_for_item(conn, i)) for i in ids if i in row_by_id]
    return items, total


def _like_prefix_pattern(prefix: str) -> str:
    return (
        prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    )


def list_tag_names(
    conn: sqlite3.Connection,
    *,
    q: str | None = None,
    limit: int = 50,
    item_status: ItemStatus | None = None,
) -> list[str]:
    prefix = (q or "").strip()
    params: list[Any] = []
    if item_status is not None:
        where = ["i.item_status = ?"]
        params.append(item_status.value)
        if prefix:
            where.append("t.name LIKE ? ESCAPE '\\'")
            params.append(_like_prefix_pattern(prefix))
        where_sql = " AND ".join(where)
        sql = f"""
            SELECT DISTINCT t.name
            FROM tags t
            JOIN item_tags it ON it.tag_id = t.id
            JOIN items i ON i.id = it.item_id
            WHERE {where_sql}
            ORDER BY t.name
            LIMIT ?
        """
        params.append(limit)
    else:
        if prefix:
            sql = """
                SELECT t.name FROM tags t
                WHERE t.name LIKE ? ESCAPE '\\'
                ORDER BY t.name
                LIMIT ?
            """
            params = [_like_prefix_pattern(prefix), limit]
        else:
            sql = """
                SELECT t.name FROM tags t
                ORDER BY t.name
                LIMIT ?
            """
            params = [limit]
    rows = conn.execute(sql, params).fetchall()
    return [r[0] for r in rows]


def soft_delete_item(conn: sqlite3.Connection, item_id: str) -> None:
    update_item_row(conn, item_id, item_status=ItemStatus.deleted)


def archive_item(conn: sqlite3.Connection, item_id: str) -> None:
    now = utc_now_iso()
    update_item_row(conn, item_id, item_status=ItemStatus.archived, read_at=now)


def hard_delete_item(conn: sqlite3.Connection, item_id: str, data_dir: Path) -> bool:
    row = conn.execute(
        "SELECT article_path, pdf_path FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    if row is None:
        return False
    d = dict(row)
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    for key in ("article_path", "pdf_path"):
        p = d.get(key)
        if p:
            fp = data_dir / p
            if fp.is_file():
                fp.unlink()
    return True
