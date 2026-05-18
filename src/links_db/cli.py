from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
import typer

from links_db import db, ingest
from links_db.models import ItemStatus, SortOption
from links_db.settings import get_settings

app = typer.Typer(no_args_is_help=True)


def _api_base() -> str:
    return os.environ.get("LINKS_API_BASE", "").rstrip("/")


def _use_http() -> bool:
    return bool(_api_base())


def _reader_base(settings) -> str:
    if settings.reader_base_url:
        return settings.reader_base_url.rstrip("/")
    return f"http://{settings.host}:{settings.port}"


@contextlib.contextmanager
def _db_session():
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db.ensure_data_dirs(settings.data_dir)
    conn = db.connect(settings.resolved_db_path())
    db.init_db(conn)
    try:
        yield conn, settings
    finally:
        conn.close()


def _http(method: str, path: str, **kwargs: Any) -> httpx.Response:
    base = _api_base()
    url = f"{base}{path}"
    with httpx.Client(timeout=120.0) as client:
        return client.request(method, url, **kwargs)


@app.command()
def add(
    url: str = typer.Argument(...),
    tags: str = typer.Option("", "--tags", "-t", help="Comma-separated tags"),
) -> None:
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    if _use_http():
        r = _http(
            "POST",
            "/items",
            json={"url": url, "tags": tag_list},
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        item = r.json()
        typer.echo(f"Saved {item['id']} ({item.get('title') or url})")
        return
    with _db_session() as (conn, st):
        item, merged = ingest.ingest_new_item(conn, st.data_dir, st, url, tag_list)
        typer.echo(
            f"{'Merged into' if merged else 'Created'} {item.id} ({item.title or url})"
        )


@app.command("list")
def list_cmd(
    status: str = typer.Option(
        "inbox",
        "--status",
        "-s",
        help="inbox | archived | deleted | read (inbox items with read_at set)",
    ),
    tag: list[str] = typer.Option(
        [],
        "--tag",
        help="Filter by tag, OR if repeated (normalized like the server)",
    ),
    q: str | None = typer.Option(None, "--query", "-q", help="Search title or URL (substring)"),
    limit: int = typer.Option(50, "--limit", "-n"),
    offset: int = typer.Option(0, "--offset"),
) -> None:
    status_l = status.strip().lower()
    if status_l == "read":
        st = ItemStatus.inbox
        require_read_at: bool | None = True
    else:
        st = ItemStatus(status_l)
        require_read_at = None

    tag_list = [t.strip() for t in tag if t.strip()] or None

    if _use_http():
        pairs: list[tuple[str, str]] = [
            ("item_status", st.value),
            ("limit", str(limit)),
            ("offset", str(offset)),
        ]
        if require_read_at is True:
            pairs.append(("require_read_at", "true"))
        for t in tag_list or []:
            pairs.append(("tag", t))
        if q and q.strip():
            pairs.append(("q", q.strip()))
        r = _http("GET", "/items", params=pairs, headers={"Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        for it in data["items"]:
            typer.echo(f"{it['id']}\t{it.get('title') or it['url']}\t{it['kind']}")
        typer.echo(f"— total {data['total']}")
        return

    with _db_session() as (conn, _):
        items, total = db.list_items(
            conn,
            item_status=st,
            tags=tag_list,
            q=q,
            require_read_at=require_read_at,
            limit=limit,
            offset=offset,
            sort=SortOption.added_at_desc,
        )
        for it in items:
            typer.echo(f"{it.id}\t{it.title or it.url}\t{it.kind.value}")
        typer.echo(f"— total {total}")


@app.command()
def show(item_id: str = typer.Argument(...)) -> None:
    if _use_http():
        r = _http("GET", f"/items/{item_id}", headers={"Accept": "application/json"})
        r.raise_for_status()
        typer.echo(json.dumps(r.json(), indent=2))
        return
    with _db_session() as (conn, _):
        item = db.get_item_by_id(conn, item_id)
        if item is None:
            typer.echo("Not found", err=True)
            raise typer.Exit(1)
        typer.echo(item.model_dump_json(indent=2))


@app.command()
def archive(item_id: str = typer.Argument(...)) -> None:
    if _use_http():
        r = _http(
            "POST",
            f"/items/{item_id}/archive",
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        typer.echo("Archived.")
        return
    with _db_session() as (conn, _):
        if db.get_item_by_id(conn, item_id) is None:
            typer.echo("Not found", err=True)
            raise typer.Exit(1)
        db.archive_item(conn, item_id)
        conn.commit()
        typer.echo("Archived.")


@app.command("open")
def open_cmd(item_id: str = typer.Argument(..., metavar="ITEM_ID")) -> None:
    settings = get_settings()
    if _use_http():
        r = _http("GET", f"/items/{item_id}", headers={"Accept": "application/json"})
        r.raise_for_status()
        item = r.json()
        base = _reader_base(settings)
        if item["kind"] == "pdf":
            cr = _http("GET", f"/items/{item_id}/content")
            cr.raise_for_status()
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(cr.content)
                path = tmp.name
            _open_path(path)
        else:
            url = f"{base}/items/{item_id}/content"
            _open_url(url)
        return
    with _db_session() as (conn, st):
        item = db.get_item_by_id(conn, item_id)
        if item is None:
            typer.echo("Not found", err=True)
            raise typer.Exit(1)
        if item.kind.value == "pdf" and item.pdf_path:
            path = st.data_dir / item.pdf_path
            _open_path(path)
        else:
            url = f"{_reader_base(st)}/items/{item_id}/content"
            _open_url(url)


def _open_url(url: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", url], check=False)
    elif sys.platform.startswith("win"):
        os.startfile(url)  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", url], check=False)


def _open_path(path: Path | str) -> None:
    p = Path(path)
    if sys.platform == "darwin":
        subprocess.run(["open", str(p)], check=False)
    elif sys.platform.startswith("win"):
        os.startfile(str(p))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(p)], check=False)


@app.command()
def tags(item_id: str = typer.Argument(...)) -> None:
    if _use_http():
        r = _http("GET", f"/items/{item_id}", headers={"Accept": "application/json"})
        r.raise_for_status()
        item = r.json()
        for t in item.get("tags", []):
            typer.echo(t)
        return
    with _db_session() as (conn, _):
        item = db.get_item_by_id(conn, item_id)
        if item is None:
            typer.echo("Not found", err=True)
            raise typer.Exit(1)
        for t in item.tags:
            typer.echo(t)


@app.command()
def reingest(item_id: str = typer.Argument(...)) -> None:
    if _use_http():
        r = _http(
            "POST",
            f"/items/{item_id}/reingest",
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        typer.echo("Reingested.")
        return
    with _db_session() as (conn, st):
        if db.get_item_by_id(conn, item_id) is None:
            typer.echo("Not found", err=True)
            raise typer.Exit(1)
        ingest.reingest_item(conn, st.data_dir, st, item_id)
        typer.echo("Reingested.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
