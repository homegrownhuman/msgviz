/* msgviz frontend bootstrap — loaded before every other script.
 *
 * Sets window.MSGVIZ.base to the URL prefix the frontend is mounted
 * under. Default is the empty string (standalone install at "/"). In
 * sub-mount mode, the server's HTML template renderer injects a value
 * like "/messages" so the frontend can talk to the right backend
 * regardless of where the app is hosted.
 *
 * Helpers exposed on `window`:
 *   mvUrl(path)          → base + path  (path expected to start with "/")
 *   mvApi(path, init)    → fetch(mvUrl(path), init)
 *
 * With these, a single call like
 *   mvApi('/api/index')
 * works identically under
 *   https://example.com/
 * and under
 *   https://example.com/messages/
 * — no hard-coded paths anywhere in the rest of the frontend.
 *
 * IIFE-wrapped so the local `W` reference doesn't leak; the only
 * globals we add are window.MSGVIZ, window.mvUrl and window.mvApi.
 */
(function () {
  var W = window;
  // The template renderer may have already set MSGVIZ.base via inline
  // <script>; only initialise it if that didn't happen.
  W.MSGVIZ = W.MSGVIZ || { base: "" };

  // Normalize: strip trailing slashes so mvUrl("/api/x") always
  // produces exactly one "/" between base and path. Without this,
  // `base="/messages/" + "/api/x"` would yield "/messages//api/x".
  W.MSGVIZ.base = (W.MSGVIZ.base || "").replace(/\/+$/, "");

  // Resolve a path against the mount base.
  // Examples (base = "/messages"):
  //   mvUrl()              → "/messages/"
  //   mvUrl("/api/index")  → "/messages/api/index"
  //   mvUrl("api/index")   → "/messages/api/index"   (leading slash added)
  W.mvUrl = function (path) {
    if (!path) return W.MSGVIZ.base + "/";
    if (path.charAt(0) !== "/") path = "/" + path;
    return W.MSGVIZ.base + path;
  };

  // Thin convenience wrapper around fetch() that resolves the URL
  // through mvUrl(). Use mvApi() for every API call from the frontend
  // (index.js, chat.js, …) so sub-mount routing keeps working.
  W.mvApi = function (path, init) {
    return fetch(W.mvUrl(path), init);
  };
})();
