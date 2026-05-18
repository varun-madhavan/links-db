import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx

from links_db import db, ingest
from links_db.settings import Settings


def _html_response(request: httpx.Request) -> httpx.Response:
    html = """<!DOCTYPE html><html><head><title>T</title></head><body><article><p>""" + (
        "word " * 400
    ) + """</p></article></body></html>"""
    return httpx.Response(
        200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        text=html,
        request=request,
    )


def test_re_add_same_canonical_merges_tags() -> None:
    transport = httpx.MockTransport(_html_response)

    def client_factory(_s: Settings) -> httpx.Client:
        return httpx.Client(
            transport=transport,
            follow_redirects=True,
            timeout=5.0,
            headers={"User-Agent": "test"},
        )

    with tempfile.TemporaryDirectory() as td:
        data = Path(td)
        db_path = data / "t.db"
        conn = db.connect(db_path)
        db.init_db(conn)
        st = Settings(data_dir=data)
        with patch("links_db.ingest._client", client_factory):
            a, m1 = ingest.ingest_new_item(
                conn, data, st, "https://example.com/a", ["news"]
            )
            assert not m1
            b, m2 = ingest.ingest_new_item(
                conn, data, st, "https://example.com/a", ["paper"]
            )
            assert m2
            assert a.id == b.id
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT COUNT(*) AS c FROM items").fetchone()
            assert int(rows["c"]) == 1
            item = db.get_item_by_id(conn, a.id)
            assert item is not None
            names = set(item.tags)
            assert names == {"news", "paper"}
