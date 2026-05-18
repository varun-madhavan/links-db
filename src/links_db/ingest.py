from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import urlparse

import httpx
import nh3
import trafilatura
from readability import Document
from ulid import ULID

from links_db import db
from links_db.models import FetchStatus, Item, ItemKind, ItemStatus, utc_now_iso
from links_db.settings import Settings


def _client(settings: Settings) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": settings.user_agent},
        follow_redirects=True,
        timeout=settings.fetch_timeout_s,
    )


def is_pdf_path(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.rstrip("/").endswith(".pdf")


def is_pdf_content_type(content_type: str | None) -> bool:
    if not content_type:
        return False
    return "application/pdf" in content_type.lower()


def is_pdf_magic(head: bytes) -> bool:
    return head.startswith(b"%PDF")


def _strip_html_for_length(html: str) -> str:
    text = trafilatura.extract(html, output_format="txt") or ""
    return text.strip()


def _sanitize_html_fragment(html: str) -> str:
    return nh3.clean(html)


def extract_metadata(html: str, final_url: str) -> tuple[str | None, str | None]:
    """Prefer Open Graph, then <title>, then trafilatura metadata (per product plan)."""
    title = _meta_content(html, "og:title")
    summary = _meta_content(html, "og:description")
    if not title:
        title = _parse_title_tag(html)
    if not title or not summary:
        meta = trafilatura.extract_metadata(html, default_url=final_url)
        if meta:
            if not title:
                title = meta.title
            if not summary:
                summary = meta.description
    return (title.strip() if title else None, summary.strip() if summary else None)


def _meta_content(html: str, prop: str) -> str | None:
    needle = f'property="{prop}"'
    idx = html.lower().find(needle.lower())
    if idx == -1:
        return None
    chunk = html[idx : idx + 400]
    cidx = chunk.lower().find('content="')
    if cidx == -1:
        return None
    start = idx + cidx + len('content="')
    end = html.find('"', start)
    if end == -1:
        return None
    return html[start:end]


def _parse_title_tag(html: str) -> str | None:
    low = html.lower()
    i = low.find("<title")
    if i == -1:
        return None
    gt = html.find(">", i)
    if gt == -1:
        return None
    close_token = chr(60) + "/title>"
    j = low.find(close_token.lower(), gt)
    if j == -1:
        return None
    return html[gt + 1 : j].strip()


def extract_html_article(html: str, settings: Settings) -> tuple[str | None, str | None]:
    primary = trafilatura.extract(
        html,
        include_comments=False,
        output_format="html",
        url=None,
    )
    chosen = primary
    text_len = len(_strip_html_for_length(chosen)) if chosen else 0
    if not chosen or text_len < settings.min_extracted_text_chars:
        try:
            doc = Document(html)
            summary_html = doc.summary()
            if summary_html:
                chosen = summary_html
                text_len = len(_strip_html_for_length(chosen))
        except Exception:
            chosen = None
    if not chosen or text_len < settings.min_extracted_text_chars:
        return None, None
    safe = _sanitize_html_fragment(chosen)
    plain = trafilatura.extract(chosen, output_format="txt") or ""
    plain = plain.strip() or None
    return safe, plain


def _read_ingest_body(
    resp: httpx.Response,
    canonical: str,
    content_type: str | None,
    settings: Settings,
) -> tuple[bytes, bool]:
    """
    Read the full response body in one pass (httpx forbids calling iter_bytes() twice).

    Until we have 4 bytes we only enforce the PDF cap so we can read the magic prefix.
    After classification we enforce the appropriate cap for the rest of the download.
    """
    buf = bytearray()
    pdfish = False
    classified = False
    max_allowed = settings.max_pdf_bytes

    for chunk in resp.iter_bytes():
        buf.extend(chunk)
        if not classified and len(buf) >= 4:
            pdfish = (
                is_pdf_path(canonical)
                or is_pdf_content_type(content_type)
                or is_pdf_magic(bytes(buf[:4]))
            )
            max_allowed = settings.max_pdf_bytes if pdfish else settings.max_html_bytes
            classified = True
        if classified and len(buf) > max_allowed:
            resp.close()
            raise ValueError("response exceeds size cap")
        if not classified and len(buf) > settings.max_pdf_bytes:
            resp.close()
            raise ValueError("response exceeds size cap")

    if not classified:
        pdfish = (
            is_pdf_path(canonical)
            or is_pdf_content_type(content_type)
            or (len(buf) >= 4 and is_pdf_magic(bytes(buf[:4])))
        )
        max_allowed = settings.max_pdf_bytes if pdfish else settings.max_html_bytes
    if len(buf) > max_allowed:
        resp.close()
        raise ValueError("response exceeds size cap")
    return bytes(buf), pdfish


def ingest_new_item(
    conn: sqlite3.Connection,
    data_dir: Path,
    settings: Settings,
    url: str,
    tags: list[str],
) -> tuple[Item, bool]:
    """
    Create or merge by canonical URL. Returns (item, merged_duplicate).
    """
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("Only http(s) URLs are supported")

    db.ensure_data_dirs(data_dir)

    with _client(settings) as client:
        with client.stream("GET", url) as resp:
            try:
                resp.raise_for_status()
            except httpx.HTTPError:
                now = utc_now_iso()
                item_id = str(ULID())
                item = db.insert_item(
                    conn,
                    item_id=item_id,
                    url=url,
                    canonical_url=url,
                    title=None,
                    summary=None,
                    kind=ItemKind.link_only,
                    fetch_status=FetchStatus.failed,
                    item_status=ItemStatus.inbox,
                    article_path=None,
                    pdf_path=None,
                    content_text=None,
                    read_at=None,
                    now=now,
                    tags=tags,
                )
                return item, False

            canonical = str(resp.url)
            ct = resp.headers.get("content-type")
            existing = db.get_item_by_canonical_url(conn, canonical)
            if existing:
                db.union_item_tags(conn, existing.id, tags)
                db.update_item_row(conn, existing.id, url=url)
                conn.commit()
                resp.close()
                item = db.get_item_by_id(conn, existing.id)
                assert item is not None
                return item, True

            body, pdfish = _read_ingest_body(resp, canonical, ct, settings)

    item_id = str(ULID())
    now = utc_now_iso()
    article_path: str | None = None
    pdf_path: str | None = None
    content_text: str | None = None
    title: str | None = None
    summary: str | None = None
    kind = ItemKind.link_only
    fetch_status = FetchStatus.partial

    if pdfish:
        rel = f"pdfs/{item_id}.pdf"
        dest = data_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        pdf_path = rel
        kind = ItemKind.pdf
        fetch_status = FetchStatus.ok
        title = Path(urlparse(canonical).path).name or "document.pdf"
    else:
        try:
            text = body.decode("utf-8", errors="replace")
        except Exception:
            text = ""
        title, summary = extract_metadata(text, canonical)
        html_body, plain = extract_html_article(text, settings)
        if html_body:
            rel = f"articles/{item_id}.html"
            dest = data_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(html_body, encoding="utf-8")
            article_path = rel
            content_text = plain
            kind = ItemKind.article
            fetch_status = FetchStatus.ok
        else:
            fetch_status = (
                FetchStatus.partial if (title or summary) else FetchStatus.failed
            )

    item = db.insert_item(
        conn,
        item_id=item_id,
        url=url,
        canonical_url=canonical,
        title=title,
        summary=summary,
        kind=kind,
        fetch_status=fetch_status,
        item_status=ItemStatus.inbox,
        article_path=article_path,
        pdf_path=pdf_path,
        content_text=content_text,
        read_at=None,
        now=now,
        tags=tags,
    )
    return item, False


def reingest_item(
    conn: sqlite3.Connection,
    data_dir: Path,
    settings: Settings,
    item_id: str,
) -> Item | None:
    row = db.get_item_by_id(conn, item_id)
    if row is None:
        return None
    target = row.canonical_url
    db.ensure_data_dirs(data_dir)

    try:
        with _client(settings) as client:
            with client.stream("GET", target) as resp:
                try:
                    resp.raise_for_status()
                except httpx.HTTPError:
                    db.update_item_row(
                        conn,
                        item_id,
                        fetch_status=FetchStatus.failed,
                        clear_article=True,
                        clear_pdf=True,
                        clear_content_text=True,
                    )
                    conn.commit()
                    return db.get_item_by_id(conn, item_id)

                canonical = str(resp.url)
                ct = resp.headers.get("content-type")
                body, pdfish = _read_ingest_body(resp, canonical, ct, settings)
    except ValueError:
        conn.commit()
        return db.get_item_by_id(conn, item_id)

    old_article = row.article_path
    old_pdf = row.pdf_path
    if old_article:
        p = data_dir / old_article
        if p.is_file():
            p.unlink()
    if old_pdf:
        p = data_dir / old_pdf
        if p.is_file():
            p.unlink()

    article_path: str | None = None
    pdf_path: str | None = None
    content_text: str | None = None
    title: str | None = None
    summary: str | None = None
    kind = ItemKind.link_only
    fetch_status = FetchStatus.partial

    if pdfish:
        rel = f"pdfs/{item_id}.pdf"
        dest = data_dir / rel
        dest.write_bytes(body)
        pdf_path = rel
        kind = ItemKind.pdf
        fetch_status = FetchStatus.ok
        title = Path(urlparse(canonical).path).name or row.title
    else:
        text = body.decode("utf-8", errors="replace")
        title, summary = extract_metadata(text, canonical)
        html_body, plain = extract_html_article(text, settings)
        if html_body:
            rel = f"articles/{item_id}.html"
            dest = data_dir / rel
            dest.write_text(html_body, encoding="utf-8")
            article_path = rel
            content_text = plain
            kind = ItemKind.article
            fetch_status = FetchStatus.ok
        else:
            title = title or row.title
            summary = summary or row.summary

    now = utc_now_iso()
    conn.execute(
        """
        UPDATE items SET
          canonical_url = ?,
          title = ?,
          summary = ?,
          kind = ?,
          fetch_status = ?,
          article_path = ?,
          pdf_path = ?,
          content_text = ?,
          updated_at = ?
        WHERE id = ?
        """,
        (
            canonical,
            title,
            summary,
            kind.value,
            fetch_status.value,
            article_path,
            pdf_path,
            content_text,
            now,
            item_id,
        ),
    )
    conn.commit()
    return db.get_item_by_id(conn, item_id)
