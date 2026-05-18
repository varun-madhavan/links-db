from __future__ import annotations

import contextlib
import html
import sqlite3
from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from links_db import db, ingest
from links_db.models import (
    FetchStatus,
    Item,
    ItemCreate,
    ItemKind,
    ItemListResponse,
    ItemPatch,
    ItemStatus,
    SortOption,
    utc_now_iso,
)
from links_db.settings import Settings, get_settings

PKG_DIR = Path(__file__).resolve().parent


def get_conn(request: Request) -> sqlite3.Connection:
    return request.app.state.conn


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
St = Annotated[Settings, Depends(get_app_settings)]


def create_app() -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        db.ensure_data_dirs(settings.data_dir)
        conn = db.connect(settings.resolved_db_path())
        db.init_db(conn)
        app.state.conn = conn
        app.state.settings = settings
        yield
        conn.close()

    app = FastAPI(title="links_db", lifespan=lifespan)

    templates = Jinja2Templates(directory=str(PKG_DIR / "templates"))

    if (PKG_DIR / "static").is_dir():
        app.mount("/static", StaticFiles(directory=str(PKG_DIR / "static")), name="static")

    @app.post("/items", response_model=Item)
    def post_items(payload: ItemCreate, conn: Conn, st: St) -> Item:
        item, _merged = ingest.ingest_new_item(conn, st.data_dir, st, payload.url, payload.tags)
        return item

    @app.get("/items", response_model=ItemListResponse)
    def get_items(
        conn: Conn,
        tag: Annotated[list[str], Query(description="Repeat for OR filter (same as multiple `tags`)")] = [],
        tags: Annotated[list[str], Query(description="Repeat for OR filter (same as multiple `tag`)")] = [],
        q: str | None = None,
        require_read_at: bool | None = None,
        kind: ItemKind | None = None,
        fetch_status: FetchStatus | None = None,
        item_status: ItemStatus = ItemStatus.inbox,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        sort: SortOption = SortOption.added_at_desc,
    ) -> ItemListResponse:
        merged_tags = [s for s in (*tag, *tags) if (s or "").strip()] or None
        items, total = db.list_items(
            conn,
            tag=None,
            tags=merged_tags,
            q=q,
            require_read_at=require_read_at,
            kind=kind,
            fetch_status=fetch_status,
            item_status=item_status,
            limit=limit,
            offset=offset,
            sort=sort,
        )
        return ItemListResponse(items=items, total=total, limit=limit, offset=offset)

    @app.get("/tags", response_model=list[str])
    def get_tags(
        conn: Conn,
        q: str | None = None,
        limit: int = Query(50, ge=1, le=200),
        item_status: ItemStatus | None = None,
    ) -> list[str]:
        return db.list_tag_names(conn, q=q, limit=limit, item_status=item_status)

    @app.get("/items/{item_id}", response_model=Item)
    def get_item(item_id: str, conn: Conn) -> Item:
        item = db.get_item_by_id(conn, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Not found")
        return item

    @app.get("/items/{item_id}/content")
    def get_item_content(item_id: str, request: Request, conn: Conn, st: St) -> Response:
        item = db.get_item_by_id(conn, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Not found")
        if item.kind == ItemKind.pdf and item.pdf_path:
            path = st.data_dir / item.pdf_path
            if not path.is_file():
                raise HTTPException(status_code=404, detail="PDF missing on disk")
            name = Path(item.pdf_path).name
            return FileResponse(
                path,
                media_type="application/pdf",
                headers={
                    "Content-Disposition": f'inline; filename="{name}"',
                    "Content-Security-Policy": "default-src 'none'",
                },
            )
        if item.kind == ItemKind.article and item.article_path:
            path = st.data_dir / item.article_path
            if not path.is_file():
                raise HTTPException(status_code=404, detail="Article missing on disk")
            inner = path.read_text(encoding="utf-8")
            safe_title = html.escape(item.title or "Read")
            doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{safe_title}</title>
<style>
body {{ max-width: 44rem; margin: 1.5rem auto; padding: 0 1rem; font-family: system-ui, sans-serif; line-height: 1.6; }}
</style>
</head><body>
{inner}
</body></html>"""
            return HTMLResponse(
                content=doc,
                headers={
                    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
                },
            )
        raise HTTPException(status_code=404, detail="No stored content for this item")

    @app.patch("/items/{item_id}", response_model=Item)
    def patch_item(item_id: str, payload: ItemPatch, conn: Conn) -> Item:
        row = db.get_item_by_id(conn, item_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")
        if payload.title is not None:
            db.update_item_row(conn, item_id, title=payload.title)
        if payload.tags is not None:
            db.set_item_tags_replace(conn, item_id, payload.tags)
        if payload.item_status is not None:
            db.update_item_row(conn, item_id, item_status=payload.item_status)
        if payload.read_at is not None:
            db.update_item_row(conn, item_id, read_at=payload.read_at)
        conn.execute(
            "UPDATE items SET updated_at = ? WHERE id = ?",
            (utc_now_iso(), item_id),
        )
        conn.commit()
        updated = db.get_item_by_id(conn, item_id)
        assert updated is not None
        return updated

    @app.post("/items/{item_id}/archive", response_model=Item)
    def post_archive(item_id: str, conn: Conn) -> Item:
        row = db.get_item_by_id(conn, item_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")
        db.archive_item(conn, item_id)
        conn.commit()
        item = db.get_item_by_id(conn, item_id)
        assert item is not None
        return item

    @app.delete("/items/{item_id}", status_code=204)
    def delete_item(
        item_id: str,
        conn: Conn,
        st: St,
        hard: bool = Query(False, description="Permanently delete row and files"),
    ) -> Response:
        row = db.get_item_by_id(conn, item_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Not found")
        if hard:
            db.hard_delete_item(conn, item_id, st.data_dir)
        else:
            db.soft_delete_item(conn, item_id)
            conn.commit()
        return Response(status_code=204)

    @app.post("/items/{item_id}/reingest", response_model=Item)
    def post_reingest(item_id: str, conn: Conn, st: St) -> Item:
        updated = ingest.reingest_item(conn, st.data_dir, st, item_id)
        if updated is None:
            raise HTTPException(status_code=404, detail="Not found")
        return updated

    def _domain(url: str) -> str:
        from urllib.parse import urlparse

        return urlparse(url).hostname or ""

    def _list_filter_query(
        q: str | None,
        tag: list[str],
        sort: SortOption,
    ) -> str:
        pairs: list[tuple[str, str]] = []
        if q and q.strip():
            pairs.append(("q", q.strip()))
        for t in tag:
            ts = (t or "").strip()
            if ts:
                pairs.append(("tag", ts))
        pairs.append(("sort", sort.value))
        return urlencode(pairs, doseq=True)

    def _render_list(
        request: Request,
        conn: Conn,
        *,
        tab: str,
        list_path: str,
        item_status: ItemStatus,
        require_read_at: bool | None,
        tag_suggest_status: ItemStatus,
        offset: int,
        q: str | None,
        tag: list[str],
        sort: SortOption,
    ) -> HTMLResponse:
        limit = 50
        clean_tags = [t.strip() for t in tag if t.strip()]
        tag_list = clean_tags or None
        items, total = db.list_items(
            conn,
            item_status=item_status,
            tags=tag_list,
            q=q,
            require_read_at=require_read_at,
            limit=limit,
            offset=offset,
            sort=sort,
        )
        fq = _list_filter_query(q, clean_tags, sort)
        return templates.TemplateResponse(
            request,
            "list.html",
            {
                "tab": tab,
                "list_path": list_path,
                "tag_suggest_status": tag_suggest_status.value,
                "items": items,
                "total": total,
                "offset": offset,
                "limit": limit,
                "domain": _domain,
                "q": (q or "").strip(),
                "active_tags": clean_tags,
                "sort": sort.value,
                "filter_query": fq,
            },
        )

    @app.get("/", response_class=HTMLResponse)
    def ui_inbox(
        request: Request,
        conn: Conn,
        offset: int = Query(0, ge=0),
        q: str | None = None,
        tag: Annotated[list[str], Query()] = [],
        sort: SortOption = SortOption.added_at_desc,
    ) -> HTMLResponse:
        return _render_list(
            request,
            conn,
            tab="inbox",
            list_path="/",
            item_status=ItemStatus.inbox,
            require_read_at=None,
            tag_suggest_status=ItemStatus.inbox,
            offset=offset,
            q=q,
            tag=tag,
            sort=sort,
        )

    @app.get("/read-list", response_class=HTMLResponse)
    def ui_read_list(
        request: Request,
        conn: Conn,
        offset: int = Query(0, ge=0),
        q: str | None = None,
        tag: Annotated[list[str], Query()] = [],
        sort: SortOption = SortOption.added_at_desc,
    ) -> HTMLResponse:
        return _render_list(
            request,
            conn,
            tab="read",
            list_path="/read-list",
            item_status=ItemStatus.inbox,
            require_read_at=True,
            tag_suggest_status=ItemStatus.inbox,
            offset=offset,
            q=q,
            tag=tag,
            sort=sort,
        )

    @app.get("/read")
    def ui_read_tab_redirect(request: Request) -> RedirectResponse:
        """Send `/read` (no item id) to the Read tab list; reader stays at `/read/{id}`."""
        qs = request.url.query
        loc = "/read-list" + (f"?{qs}" if qs else "")
        return RedirectResponse(url=loc, status_code=307)

    @app.get("/archive", response_class=HTMLResponse)
    def ui_archive(
        request: Request,
        conn: Conn,
        offset: int = Query(0, ge=0),
        q: str | None = None,
        tag: Annotated[list[str], Query()] = [],
        sort: SortOption = SortOption.added_at_desc,
    ) -> HTMLResponse:
        return _render_list(
            request,
            conn,
            tab="archive",
            list_path="/archive",
            item_status=ItemStatus.archived,
            require_read_at=None,
            tag_suggest_status=ItemStatus.archived,
            offset=offset,
            q=q,
            tag=tag,
            sort=sort,
        )

    @app.get("/read/{item_id}", response_class=HTMLResponse)
    def ui_read(request: Request, item_id: str, conn: Conn, st: St) -> HTMLResponse:
        item = db.get_item_by_id(conn, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Not found")
        if item.read_at is None and item.item_status == ItemStatus.inbox:
            db.update_item_row(conn, item_id, read_at=utc_now_iso())
            conn.commit()
            item = db.get_item_by_id(conn, item_id)
            assert item is not None
        if item.kind == ItemKind.pdf and item.pdf_path:
            base = str(request.base_url).rstrip("/")
            return HTMLResponse(
                f'<html><body><p>PDF: <a href="{base}/items/{item_id}/content">Open inline</a></p></body></html>'
            )
        if item.kind == ItemKind.article and item.article_path:
            path = st.data_dir / item.article_path
            inner = path.read_text(encoding="utf-8") if path.is_file() else ""
            return templates.TemplateResponse(
                request,
                "reader.html",
                {"item": item, "inner_html": inner, "tab": ""},
                headers={
                    "Content-Security-Policy": (
                        "default-src 'none'; "
                        "style-src 'unsafe-inline'; "
                        "script-src 'unsafe-inline' https://unpkg.com"
                    ),
                },
            )
        raise HTTPException(status_code=404, detail="Nothing to read")

    @app.get("/item/{item_id}", response_class=HTMLResponse)
    def ui_item(request: Request, item_id: str, conn: Conn) -> HTMLResponse:
        item = db.get_item_by_id(conn, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Not found")
        return templates.TemplateResponse(
            request,
            "item.html",
            {"item": item, "tab": ""},
        )

    return app


app = create_app()
