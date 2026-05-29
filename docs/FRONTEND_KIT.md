# msgviz Frontend Kit

The `app/` directory contains the bundled web frontend (vanilla JS, no
framework, no build step). You can:

1. use it **directly** with the msgviz FastAPI server (standalone),
2. integrate it **under a sub-mount** inside a host FastAPI app,
3. host it **fully separately** as long as the msgviz API is reachable.

This doc covers option 3, plus everything you need to know to modify the
frontend or replace it with your own.

---

## File overview

```
app/
├── chat.css            # full styling (index + chat)
├── chat.js             # chat view: pagination, heatmap, media, live push
├── index.js            # index page: devices + chat cards
├── msgviz-base.js      # bootstrap: window.MSGVIZ, mvUrl(), mvApi()
├── lazysizes.min.js    # lazy loading for images
├── fontawesome/        # icon font
├── icons/              # app icons (favicon, apple-touch)
└── webfonts/           # Inter (default font)
```

Plus the HTML templates at the repo root:

* `index.html` — index page
* `chat.template.html` — chat page (slug from `location.pathname`)

Both contain the `{{base}}` placeholder that the msgviz server replaces
at render time.

---

## Bootstrap & URL resolution

The frontend uses a **single global URL helper** to be sub-mount aware.
`app/msgviz-base.js` is loaded **before** `index.js`/`chat.js` and
exposes three globals:

```js
window.MSGVIZ = { base: "" };       // set by the HTML template

window.mvUrl(path);                  // -> base + path
window.mvApi(path, init);            // -> fetch(mvUrl(path), init)
```

In the HTML template head:

```html
<script>window.MSGVIZ = { base: "{{base}}" };</script>
<script src="{{base}}/app/msgviz-base.js"></script>
```

The `{{base}}` placeholder becomes:

* `""` in standalone mode (mounted at `/`).
* `"/messages"` (or similar) when mounted via `host.mount("/messages", mv)`.

---

## Integrating in custom hosting

If you want to serve the frontend **without** the msgviz FastAPI server
(e.g. backend behind NGINX/Caddy/Cloudflare Pages), copy:

```
app/                  → /static/msgviz/
index.html            → /static/msgviz/index.html
chat.template.html    → /static/msgviz/chat.template.html
```

And resolve the `{{base}}` placeholder yourself:

```html
<!-- index.html -->
<link rel="stylesheet" href="/static/msgviz/app/chat.css">
<script>window.MSGVIZ = { base: "/api/msgviz" };</script>
<script src="/static/msgviz/app/msgviz-base.js"></script>
```

Here:

* Static assets live at `/static/msgviz/...` (NGINX/CDN).
* API calls go to `/api/msgviz/api/...` (msgviz FastAPI behind a proxy).

---

## API requirements

The frontend hits these msgviz endpoints (see [API.md](API.md)):

| Endpoint | Used in |
|---|---|
| `GET /api/index` | `index.js`, live polling on the index page |
| `GET /api/chat/{slug}/meta` | `chat.js` initial load |
| `GET /api/chat/{slug}/latest` | `chat.js` initial load |
| `GET /api/chat/{slug}/before/{ts}` | `chat.js` scroll up |
| `GET /api/chat/{slug}/since/{ts}` | `chat.js` live polling |
| `GET /api/chat/{slug}/around/{ts}` | `chat.js` heatmap jump |
| `GET /api/chat/{slug}/edited` | `chat.js` edited filter |
| `GET /api/chat/{slug}/days` | `chat.js` heatmap data |
| `GET /api/chat/{slug}/media` | `chat.js` media overview |
| `POST /api/chat/{slug}/seen` | `index.js` clear badge |
| `WS /ws` | live push (optional) |

Plus data files:

| Path | Content |
|---|---|
| `GET /data/transcripts.json` | audio transcripts (key = `media.src`) |
| `GET /data/ocr.json` | OCR results (key = `media.src`) |
| `GET /media/...` | images, audio, video |
| `GET /originals/...` | originals if stored |

---

## Building your own frontend

If the bundled UI doesn't fit (different design, your own UX, your own
language), just use the API. Vue example:

```vue
<script setup>
import { ref, onMounted } from 'vue'

const chats = ref([])

onMounted(async () => {
  const r = await fetch('/api/index')
  const data = await r.json()
  chats.value = data.chats
})
</script>

<template>
  <ul>
    <li v-for="c in chats" :key="c.slug">
      {{ c.title }} — {{ c.total }} messages
    </li>
  </ul>
</template>
```

In that case, start the bundled server **only** as an API backend
(`msgviz serve`) and run your own frontend separately. Static assets
under `/app/` aren't needed then.

---

## Styling and branding

`app/chat.css` is a single file (~40 KB). CSS custom properties at the
root make branding easy:

```css
:root {
  --bg: #1a1a1a;
  --fg: #e0e0e0;
  --accent: #3a9bff;
  /* ... */
}
```

Custom theme: create `app/theme-override.css` and load it **after**
`chat.css`. Override via higher specificity or `!important` on custom
properties.

---

## What the frontend does **not** do

* **Auth UI**: no login screen, no tokens.
* **Edit/send**: msgviz is read-only — no message composition.
* **Settings page**: configuration goes through the `msgviz` CLI and
  `config/sources.json`, not via the web UI.
* **Offline mode** (service worker style) — the app assumes online
  reachability.

---

## Tests

`tests/unit/test_frontend_kit.py` pins the sub-mount behavior:

* Standalone vs. sub-mount: correct asset paths
* `window.MSGVIZ.base` is set appropriately
* `<base href>` in the chat template is prefixed correctly
* `msgviz-base.js` is referenced in the HTML
* Static assets are reachable under sub-mount

If you fork the frontend, keep those tests in your pipeline — they're
the only guarantee that embedding doesn't silently break.
