# links_db — local read-later

This repo is a **personal Instapaper-style read-later app** that runs entirely on your machine:

- **SQLite** stores each item (URL, title, tags, inbox/archived/deleted, fetch status).
- **Disk** stores extracted article HTML (`data/articles/`) and downloaded PDFs (`data/pdfs/`).
- **Web UI** (FastAPI + Jinja + HTMX) for browsing and adding links in a browser.
- **`links` CLI** (Typer) for quick saves from the terminal **without** starting the server first.

Everything reads/writes under a configurable **`LINKS_DATA_DIR`** (default `./data` in the current working directory). Run commands from the same directory (or set `LINKS_DATA_DIR` explicitly) so the CLI and server see the same library.

---

## 1. One-time setup

From the **repository root** (`links_db/`):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Optional dev dependencies (tests):

```bash
pip install -e '.[dev]'
```

The `links` command is installed into the active venv. If `links` is not found, use:

```bash
python -m links_db.cli --help
```

---

## 2. Where your data lives

With defaults (no env vars), from your **current shell directory**:

| What | Path |
|------|------|
| Database | `./data/links.db` |
| Saved article HTML | `./data/articles/<ULID>.html` |
| Saved PDFs | `./data/pdfs/<ULID>.pdf` |

`data/` is gitignored. **Back up** `data/links.db` plus the `articles/` and `pdfs/` folders if you care about offline copies.

---

## 3. Daily use — CLI (no server required)

Activate the venv, `cd` to the folder where you want `data/` to live (or set `LINKS_DATA_DIR`).

### Add a link

```bash
links add "https://example.com/some-article"
```

Tags (comma-separated, optional):

```bash
links add "https://example.com/paper.pdf" --tags papers,ml
# short form:
links add "https://…" -t news,read-later
```

- **HTML pages:** the app fetches the page, extracts readable HTML, sanitizes it, and stores it.
- **PDF URLs:** if the response is a real PDF, it is saved under `data/pdfs/` automatically.

### List items

```bash
links list                    # inbox (default)
links list --status archived
links list --status deleted
links list --tag papers       # filter by one tag
links list -n 20 --offset 40  # pagination
```

Columns are: `id`, title (or URL), `kind` (`article` | `pdf` | `link_only`).

### Inspect one item

```bash
links show 01JABC…            # paste the ULID from `links list`
```

JSON includes `tags`, `kind`, `fetch_status`, `item_status`, paths, etc.

### List tags on an item

```bash
links tags 01JABC…
```

(One tag per line.)

### Open in browser / viewer

```bash
links open 01JABC…
```

- **Article:** opens `http://<host>:<port>/items/<id>/content` in your default browser. That URL is built from `LINKS_HOST`, `LINKS_PORT`, or **`LINKS_READER_BASE_URL`** if you set it (see below). **For articles, the server must be running** so the browser can load that URL—unless you open the file manually from `data/articles/`.
- **PDF:** opens the **local file** with the OS handler (`open` on macOS, `xdg-open` on Linux, `startfile` on Windows)—**no server needed**.

### Archive (mark read / move out of inbox)

```bash
links archive 01JABC…
```

### Re-fetch body and metadata

```bash
links reingest 01JABC…
```

Useful if the first fetch failed or the site changed.

### Help

```bash
links --help
links add --help
```

---

## 4. Daily use — Web UI

Start the server (from the directory where `data/` should live, same as CLI):

```bash
python -m links_db
```

Defaults: **`http://127.0.0.1:8765`**

| URL | Purpose |
|-----|---------|
| `http://127.0.0.1:8765/` | **Inbox** — items with `item_status=inbox` |
| `http://127.0.0.1:8765/read-list` | **Read** — inbox items that have `read_at` set (opened in the reader at least once) |
| `http://127.0.0.1:8765/archive` | **Archive** |
| `http://127.0.0.1:8765/item/<id>` | Detail page for one item |
| `http://127.0.0.1:8765/read/<id>` | Reader layout for saved HTML |
| `http://127.0.0.1:8765/read` | Redirects to **`/read-list`** (same query string preserved) |
| `http://127.0.0.1:8765/items/<id>/content` | Raw reader document (HTML wrapper or inline PDF) — what `links open` uses for articles |

**Add a link:** use the form at the top (URL + comma-separated tags). It calls `POST /items` in the background and reloads the page.

**Archive from the list:** **Archive** button uses HTMX to `POST /items/<id>/archive`.

---

## 5. CLI “HTTP mode” (optional)

If **`LINKS_API_BASE`** is set to the API root (no trailing slash), **every** `links` subcommand talks to the running server instead of opening SQLite directly:

```bash
export LINKS_API_BASE=http://127.0.0.1:8765
links list
links add "https://…"
```

Useful if you want a single long-lived process owning the DB, or you run the API on another machine (not the default setup).

**Library mode** (default): unset `LINKS_API_BASE` — CLI opens `data/links.db` directly.

---

## 6. Environment variables

Settings use the prefix **`LINKS_`** and optional **`.env`** in the cwd (`pydantic-settings`). Examples:

```bash
export LINKS_DATA_DIR="$HOME/links-data"
export LINKS_HOST=127.0.0.1
export LINKS_PORT=8765
```

| Variable | Meaning |
|----------|---------|
| `LINKS_DATA_DIR` | Root for DB + `articles/` + `pdfs/` (default `./data`) |
| `LINKS_DB_PATH` | Full path to SQLite file (overrides default `{DATA_DIR}/links.db`) |
| `LINKS_HOST` | Bind address for `python -m links_db` (default `127.0.0.1`) |
| `LINKS_PORT` | Port (default `8765`) |
| `LINKS_READER_BASE_URL` | Base URL used by **`links open`** for **articles** (default `http://{HOST}:{PORT}`) |
| `LINKS_API_BASE` | If set, CLI uses HTTP to this API root instead of library mode |
| `LINKS_FETCH_TIMEOUT_S` | HTTP timeout when fetching URLs (default `15`) |
| `LINKS_MAX_HTML_BYTES` | Max HTML download size (default 5MB) |
| `LINKS_MAX_PDF_BYTES` | Max PDF size (default 50MB) |
| `LINKS_USER_AGENT` | User-Agent string for fetches (default mimics Chrome on macOS) |
| `LINKS_MIN_EXTRACTED_TEXT_CHARS` | Minimum extracted text length before trying readability fallback (default `200`) |

---

## 7. REST API (for scripts or HTTP-mode CLI)

Base URL: `http://127.0.0.1:8765` (with your host/port).

| Method | Path | Notes |
|--------|------|------|
| `POST` | `/items` | JSON `{"url":"…","tags":["a","b"]}` — returns created or **merged** item |
| `GET` | `/items` | Query: `tag`, `kind`, `fetch_status`, `item_status` (default `inbox`), `limit`, `offset`, `sort` |
| `GET` | `/items/{id}` | Full item |
| `GET` | `/items/{id}/content` | Article HTML shell or PDF bytes |
| `PATCH` | `/items/{id}` | JSON: `title`, `tags` (**replace** all tags), `item_status`, `read_at` |
| `POST` | `/items/{id}/archive` | Sets archived + `read_at` |
| `DELETE` | `/items/{id}` | Soft delete; add `?hard=true` to delete row and files |
| `POST` | `/items/{id}/reingest` | Re-download and overwrite artifact |

Example:

```bash
curl -sS -X POST http://127.0.0.1:8765/items \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com","tags":["demo"]}' | jq .
```

---

## 8. Behavior you should remember

- **Item IDs** are **ULIDs** (string primary keys). They appear in URLs and filenames.
- **Duplicate URL:** same **canonical URL** after redirects **merges** into one row; new tags on re-add are **unioned**. To replace tags, use `PATCH /items/{id}` with a full `tags` array (or extend the UI later).
- **Statuses:** `inbox` → **Archive** moves to `archived` and sets `read_at`. **Delete** in the API is soft (`deleted`) unless `?hard=true`.
- **Fetch status:** `ok` / `partial` / `failed`. You can still have a bookmark with no body (`link_only` or partial metadata).
- Many sites block bots; the default User-Agent is already browser-like. Paywalls and heavy JS sites often **will not** archive—reingest will not fix that without a future browser-based fetcher.

---

## 9. Tests

```bash
pytest
```

---

## 10. If something goes wrong

- **CLI and server disagree:** usually different **cwd** or different **`LINKS_DATA_DIR`**. Align them or set `LINKS_DATA_DIR` to an absolute path for both.
- **`links open` for an article shows nothing:** start the server on the host/port that `LINKS_READER_BASE_URL` (or default) points to.
- **Empty article body:** site may require JS or block scrapers; try another URL or inspect `fetch_status` / `kind` with `links show`.

---

## 11. Alternative: run with uvicorn directly

```bash
uvicorn links_db.api:app --host 127.0.0.1 --port 8765
```

Same app as `python -m links_db`; env vars still apply.

---

## 12. Repo map and agent notes

For a **directory map**, module roles, and REST/HTML index, see [REPO_MAP.md](REPO_MAP.md). For **AI / agent** guidance (FastAPI `Depends`, httpx streaming, where to edit), see [AGENTS.md](AGENTS.md).
