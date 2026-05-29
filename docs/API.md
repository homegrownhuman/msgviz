# msgviz HTTP API

The msgviz backend is a thin FastAPI layer over the local SQLite DB.
It powers the bundled frontend but is also usable as a pure data API
for your own clients.

## Conventions

* **Base URL**: `http://127.0.0.1:8753/` in standalone mode.
  In embedded mode (see [EMBEDDING.md](EMBEDDING.md)), the base URL is
  `<host>/<mount-prefix>/`.
* **Format**: JSON (request + response).
* **Read-only DB connection** for all GETs. POSTs use a separate write
  connection.
* **Pagination** via `?limit=` and `before/since/{ts}`. Default 50,
  maximum 500.
* **Chat slugs**: `<device-slug>/<chat-slug>`, e.g. `my_mac/bob`.

---

## Index

### `GET /api/index`

Overview of devices and chats. Loaded by the index frontend.

**Response**:

```json
{
  "devices": [
    {
      "id": 1,
      "slug": "my_mac",
      "name": "MacBook",
      "me_name": "Alice",
      "owner_avatar": "media/avatars/1c/1ca2e4524f921933.jpg"
    }
  ],
  "chats": [
    {
      "slug": "my_mac/bob",
      "title": "Bob",
      "subtitle": "+49 1234 567890 · iMessage",
      "origin": "apple",
      "device": "MacBook",
      "device_slug": "my_mac",
      "me_name": "Alice",
      "is_group": false,
      "live": true,
      "total": 1024,
      "me": 600,
      "them": 424,
      "new_count": 3,
      "first": 1700000000,
      "last": 1730000000,
      "media": { "image": {"me": 12, "them": 8}, "video": {...}, "audio": {...}, "other": {...} },
      "bytes": { "image": 134217728, "video": 268435456, "audio": 0, "other": 0 },
      "bytes_total": 402653184,
      "bytes_orig": 0,
      "chat_avatar": "media/avatars/78/78b575cd78bf518f.jpg"
    }
  ]
}
```

`live: true` marks iMessage chats on a `mac_live` device.
`new_count` is the number of messages received since the last `/seen`
call — it drives the "unread" badge in the frontend.

**Avatars** (optional, only present when set):

* `owner_avatar` on a device — the device owner's avatar, relative path
  from the project root (e.g. `media/avatars/<prefix>/<hash>.jpg`).
* `chat_avatar` on a 1:1 chat — the counterpart's avatar. Group chats
  and 1:1 chats whose counterpart has no avatar omit the field.

Resolve avatar paths against the server root (`<base>/<avatar_src>`).

---

## Chat endpoints

All chat endpoints accept `{slug:path}`, so slashes inside the slug are
allowed — encode each path segment with `encodeURIComponent`.

### `GET /api/chat/{slug}/meta`

Chat header data: title, subtitle, statistics, media overview.

```json
{
  "slug": "my_mac/bob",
  "title": "Bob",
  "subtitle": "+49 1234 567890",
  "origin": "apple",
  "device": "MacBook",
  "me_name": "Alice",
  "is_group": false,
  "stats": {
    "total": 1024,
    "me": 600,
    "them": 424,
    "first": 1700000000,
    "last": 1730000000,
    "media": { ... },
    "bytes": { ... },
    "bytes_total": 402653184
  },
  "owner_avatar": "media/avatars/1c/1ca2e4524f921933.jpg",
  "chat_avatar": "media/avatars/78/78b575cd78bf518f.jpg"
}
```

`owner_avatar` and `chat_avatar` are present only when set (see
[`GET /api/index`](#get-apiindex)).

### `GET /api/chat/{slug}/latest?limit=50`

Most recent N messages, chronological (oldest first).

```json
{
  "messages": [
    {
      "t": "msg",
      "ts": 1730000000,
      "me": false,
      "sender": "Bob",
      "sender_avatar": "media/avatars/78/78b575cd78bf518f.jpg",
      "text": "Hello!",
      "edits": [{ "text": "Hi", "ts": 1729999990 }],
      "reactions": [{ "emoji": "❤️", "sender": "Alice" }],
      "media": [{ "kind": "image", "src": "media/images/ab/abc123.jpg" }]
    }
  ],
  "has_more": true
}
```

Optional fields: `sender_avatar`, `edits`, `reactions`, `apps`,
`retracted`, `media`. `sender_avatar` is omitted when the sender's
person has no avatar set — the frontend then renders initials.

### `GET /api/chat/{slug}/before/{ts}?limit=50`

N messages **older** than `ts`. For scroll-up pagination.

### `GET /api/chat/{slug}/since/{ts}?limit=500`

All messages **newer** than `ts`. For live polling.

### `GET /api/chat/{slug}/around/{ts}?before=40&after=80`

Block around a timestamp (jumping from heatmap to a date).

### `GET /api/chat/{slug}/edited`

**All** edited messages of a chat, regardless of the currently loaded
window. Also returns `total`.

### `GET /api/chat/{slug}/days`

Per-day histogram over the entire history. Used by the heatmap.

```json
{
  "days": {
    "2025-01-15": 12,
    "2025-01-16": 3,
    ...
  }
}
```

### `GET /api/chat/{slug}/media`

All media of a chat, chronological. For the media overview.

```json
{
  "media": [
    {
      "kind": "image",
      "src": "media/images/ab/abc123.jpg",
      "me": false,
      "sender": "Bob",
      "ts": 1730000000,
      "cap": "Check this!",
      "cat": "selfie",
      "portrait": true
    }
  ]
}
```

### `POST /api/chat/{slug}/seen`

Marks every message in the chat with `sync_state='new'` as
`'published'`. The frontend calls this when a chat is opened, clearing
the unread badge.

```json
{ "ok": true, "marked_seen": 3 }
```

---

## WebSocket

### `WS /ws`

Live push for new messages (`mac_live` sources) and DB connection status.

**Server push types**:

```json
{ "type": "dbstatus", "online": true }
{ "type": "update", "chats": [{"slug": "my_mac/bob", "new": 2, "last": 1730000123}] }
```

Clients don't have to send anything — all messages come from the server.
`dbstatus` is broadcast when Apple's `chat.db` reachability changes,
`update` after a sync round that produced new messages.

On non-Darwin systems the watcher doesn't run — the WebSocket stays
open but only emits the initial `dbstatus`.

---

## Static routes

| Path | Content |
|---|---|
| `/` | Index HTML (rendered with the `base` placeholder) |
| `/chat/{slug}` | Chat template HTML |
| `/favicon.ico` | App icon |
| `/app/...` | CSS, JS, fonts, icons |
| `/data/...` | Frontend data files (transcripts.json, ocr.json) |
| `/media/...` | Hash-based media (images/audio/video) |
| `/originals/...` | Original resolutions if stored |

Mount paths are configurable in embedded mode (see
[EMBEDDING.md](EMBEDDING.md)).

---

## CORS, auth, HTTPS

The API is **not** designed for public exposure:
* No CORS headers.
* No authentication.
* No TLS termination in uvicorn (use a reverse proxy like Caddy locally).

If you expose the server publicly, it's your responsibility to add an
auth layer — easiest as FastAPI middleware in the host app (see
[EMBEDDING.md](EMBEDDING.md)).
