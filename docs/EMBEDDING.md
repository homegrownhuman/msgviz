# Embedding msgviz

`msgviz` is a FastAPI app. Standalone mode (`msgviz serve`) is enough
for local use — but if you want to **embed msgviz in an existing web
app** (your own domain, your own auth, your own reverse proxy), use
the factory directly.

## TL;DR

```python
from fastapi import FastAPI
from msgviz.config import MVConfig
from msgviz.server.factory import create_app

host = FastAPI()

@host.get("/")
def root():
    return {"app": "my own server"}

# mount msgviz as a sub-app under /messages
mv = create_app(MVConfig(
    db_file="/var/lib/msgviz/visualizer.db",
    media_root="/var/lib/msgviz/media",
    enable_watcher=False,
))
host.mount("/messages", mv)
```

Result:

* `GET /` → host app
* `GET /messages/` → msgviz index page
* `GET /messages/api/index` → JSON API
* `GET /messages/app/chat.css` → static assets

The frontend detects the mount prefix automatically via
`request.scope.root_path` and prefixes every asset path and API call
accordingly.

---

## `MVConfig`

Everything `msgviz` needs lives in `MVConfig`. Defaults come from
`msgviz.paths.default_config()` (repo root or `MSGVIZ_HOME`):

| Field | Default | Meaning |
|---|---|---|
| `project_root` | `msgviz.paths.project_root()` | Root directory |
| `data_dir` | `<root>/data` | DB + JSON caches |
| `db_file` | `<root>/data/visualizer.db` | SQLite DB |
| `media_root` | `<root>/media` | Hash-based media |
| `originals_root` | `<root>/originals` | Originals (optional) |
| `app_dir` | `<root>/app` | Static web assets |
| `config_dir` | `<root>/config` | sources.json |
| `sources_json` | `<config>/sources.json` | Devices/chats config |
| `index_html` | `<root>/index.html` | Index template |
| `chat_template_html` | `<root>/chat.template.html` | Chat template |
| `favicon_path` | `<app>/icons/favicon.ico` | App icon |
| `default_page_limit` | `50` | API pagination |
| `max_page_limit` | `500` | Upper bound |
| `watcher_poll_seconds` | `2.0` | Live-sync interval |
| `enable_watcher` | `True` | Live watcher on/off |
| `mount_app` | `/app` | Asset mount path |
| `mount_data` | `/data` | Data mount path |
| `mount_media` | `/media` | Media mount path |
| `mount_originals` | `/originals` | Originals mount path |
| `nocache_app_files` | `True` | Cache-Control: no-cache for `/app/*` |
| `title` | `"msgviz"` | FastAPI title |
| `description` | … | FastAPI description |

Path fields accept `Path` or `str`.

---

## Typical embedding patterns

### 1. Mount sub-app under a prefix

As in the TL;DR. The frontend bootstrap (`app/msgviz-base.js`) reads
the mount prefix automatically.

### 2. msgviz with auth middleware

```python
from fastapi import FastAPI, Request, HTTPException
from msgviz.server.factory import create_app

host = FastAPI()
mv = create_app()

@host.middleware("http")
async def require_auth(request: Request, call_next):
    if request.url.path.startswith("/messages"):
        if not request.headers.get("X-Auth-Token"):
            raise HTTPException(401)
    return await call_next(request)

host.mount("/messages", mv)
```

### 3. msgviz with a fully custom frontend

If you don't want the bundled frontend, mount only the API endpoints
and ignore the HTML/static mounts. API routes are self-contained:

```python
mv = create_app(MVConfig(
    # API is enough — the static mounts come along but don't hurt.
))
host.mount("/api/messages", mv)

# In your own frontend then e.g.:
fetch("/api/messages/api/chat/my_mac/bob/latest")
```

### 4. Multiple msgviz instances side by side

```python
from msgviz.config import MVConfig
from msgviz.server.factory import create_app

prod = create_app(MVConfig(
    db_file="/var/lib/msgviz-prod/db.sqlite",
    media_root="/var/lib/msgviz-prod/media",
    title="msgviz · production",
))
test = create_app(MVConfig(
    db_file="/var/lib/msgviz-test/db.sqlite",
    media_root="/var/lib/msgviz-test/media",
    title="msgviz · test",
    enable_watcher=False,
))

host.mount("/prod", prod)
host.mount("/test", test)
```

Each instance has its own DB, its own watcher, its own `ServerState` —
no state sharing.

### 5. Reverse proxy with path rewrite

If your reverse proxy (nginx, Caddy) already strips the prefix, run
the backend with `--root-path /messages` so it knows to put the
prefix back into the URLs it emits in HTML.

**Caddy:**

```caddyfile
example.com {
    # Sub-mount: strip /messages/ before proxying, the backend was
    # told the prefix via --root-path /messages and re-emits it
    # in every asset URL it generates.
    handle_path /messages/* {
        reverse_proxy 127.0.0.1:8753
    }

    # … other routes here …
}
```

Start the backend with the matching root-path:

```bash
msgviz serve --host 127.0.0.1 --port 8753 --root-path /messages
```

**nginx:** the equivalent pattern — `proxy_pass` with a trailing slash
strips the prefix:

```nginx
location /messages/ {
    proxy_pass http://127.0.0.1:8753/;        # ← trailing slash strips prefix
}
```

Internally, `--root-path` is forwarded to uvicorn's ASGI `root_path`,
which the template renderer reads as `request.scope['root_path']` and
substitutes into every `{{base}}` in the HTML.

### Worked example: two msgviz instances on one hostname

A common local-dev setup: live archive at the root, demo dataset
under `/dev/`. Two uvicorn processes, two `MSGVIZ_HOME` values, one
Caddy block:

```caddyfile
messages.example.com {
    handle_path /dev/* {
        reverse_proxy 127.0.0.1:8754   # demo: MSGVIZ_HOME=demo --root-path /dev
    }
    reverse_proxy 127.0.0.1:8753       # live: MSGVIZ_HOME=data  (no root-path)
}
```

```bash
# live archive at the root
msgviz serve --host 127.0.0.1 --port 8753

# demo dataset under /dev/
MSGVIZ_HOME=demo msgviz serve --host 127.0.0.1 --port 8754 --root-path /dev
```

---

## Reading server state

`create_app()` attaches two objects to `app.state`:

* `app.state.mv_config` — the `MVConfig` instance.
* `app.state.mv_state` — the `ServerState` (DB connection factory, Hub, etc.).

You can use them e.g. in a host route to read msgviz stats:

```python
@host.get("/admin/msgviz-stats")
def stats():
    state = mv.state.mv_state
    with state.db() as con:
        n = con.execute("SELECT COUNT(*) FROM message").fetchone()[0]
    return {"messages": n}
```

---

## Path configuration: `MSGVIZ_HOME`

Instead of setting every `MVConfig` field, set `MSGVIZ_HOME`. Then
`default_config()` picks all paths automatically.

```bash
MSGVIZ_HOME=/var/lib/msgviz python -m msgviz serve
```

In code:

```python
import os
os.environ["MSGVIZ_HOME"] = "/var/lib/msgviz"
from msgviz.server.factory import create_app
mv = create_app()  # all paths derived from MSGVIZ_HOME
```

---

## What embedding does **not** solve

* **DB migrations**: msgviz expects `data/visualizer.db` to exist with
  the current schema. Run `msgviz init` before first start.
* **Authentication**: msgviz has no auth of its own — you add it in the
  host app.
* **HTTPS**: msgviz speaks HTTP. Let your reverse proxy do TLS.
* **Multi-user separation**: one msgviz instance per dataset, not per user.
