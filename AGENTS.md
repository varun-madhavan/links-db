# Agent guide — links_db

Use this file with [REPO_MAP.md](REPO_MAP.md) to navigate and change this repository safely.

---

## What this project is

A **single-user local read-later** tool: save URLs with tags, fetch and store **sanitized article HTML** or **PDFs**, track **inbox / archived / deleted**, expose a **FastAPI** app (JSON + minimal HTML UI) and a **`links` CLI** that works **without** the server unless `LINKS_API_BASE` is set.

---

## Where to look first

| Task | Start in |
|------|----------|
| Change fetch / extract / PDF logic | [src/links_db/ingest.py](src/links_db/ingest.py) |
| New REST behavior or HTML page | [src/links_db/api.py](src/links_db/api.py) |
| Schema, queries, tags, pagination | [src/links_db/db.py](src/links_db/db.py) |
| API shapes / enums | [src/links_db/models.py](src/links_db/models.py) |
| Env vars / defaults | [src/links_db/settings.py](src/links_db/settings.py) |
| CLI commands | [src/links_db/cli.py](src/links_db/cli.py) |
| List / reader / item templates | [src/links_db/templates/](src/links_db/templates/) |
| User-facing how-to | [README.md](README.md) |

---

## Invariants (do not regress)

1. **`api.py` dependency aliases** — `Conn` and `St` (`Annotated[..., Depends(...)]`) **must remain at module level** because of `from __future__ import annotations`. If moved inside `create_app()`, `GET /` returns 422 asking for query param `conn`.

2. **Single-pass HTTP body reads** — In `ingest.py`, the response stream must be consumed **once** (see `_read_ingest_body`). A second `iter_bytes()` causes `httpx.StreamConsumed` and 500s on `POST /items`.

3. **Artifact paths** — Only use internal ULID-based relative paths under `data_dir`; never path segments from user input.

4. **SQLite connection** — The server uses one long-lived `app.state.conn` (`check_same_thread=False`). Keep route handlers quick; ingest runs in the same thread as the route (blocking); acceptable for local MVP.

---

## Conventions for edits

- Match existing style: minimal comments, type hints, no drive-by refactors.
- Run **`pytest`** from repo root after behavioral changes to ingest/db/api.
- Do not commit `data/` or `.venv/` (gitignored).
- Optional `.agents/skills/` content is **not** imported by the app; ignore unless the user asks about skills.

---

## Testing

- **No network by default** in CI-style runs: `test_pdf_detection` (pure helpers), `test_extraction` (fixture file), `test_duplicates` (MockTransport + `patch` on `ingest._client`).
- Adding tests that hit the real network: mark or gate them; current suite avoids that.

---

## Extension ideas (not implemented)

FTS5 search, Playwright for JS sites, asset archiving, bookmarklet, auth if bound beyond localhost—see README limitations.
