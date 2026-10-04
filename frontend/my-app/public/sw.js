/*
 * NoteScanner service worker: makes the app installable and keeps the app shell available offline.
 *
 * - Only same-origin GET requests inside this app's scope are handled. API calls go to the backend's
 *   own origin and are never touched or cached.
 * - Page loads (navigation) are network-first, so a new release is picked up as soon as the user is
 *   online; the cached page is only a fallback when offline.
 * - Built files under /assets/ have content hashes in their names, so they're cached on first use
 *   (cache-first) and old ones are trimmed.
 * - Bump VERSION to drop every cache from older service workers on activate.
 */
const VERSION = "v1";
const SHELL_CACHE = `notescanner-shell-${VERSION}`;
const ASSET_CACHE = `notescanner-assets-${VERSION}`;
const MAX_ASSETS = 60;
const SCOPE = new URL(self.registration.scope);
const SHELL_URLS = ["./", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png"].map(
  (p) => new URL(p, SCOPE).toString(),
);

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL_URLS))
      .catch(() => undefined)
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k.startsWith("notescanner-") && k !== SHELL_CACHE && k !== ASSET_CACHE)
            .map((k) => caches.delete(k)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

async function trimAssets() {
  const cache = await caches.open(ASSET_CACHE);
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - MAX_ASSETS; i++) await cache.delete(keys[i]);
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== SCOPE.origin || !url.pathname.startsWith(SCOPE.pathname)) return;

  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(SHELL_CACHE).then((cache) => cache.put(SHELL_URLS[0], copy));
          }
          return res;
        })
        .catch(() => caches.match(SHELL_URLS[0]).then((cached) => cached || Response.error())),
    );
    return;
  }

  if (url.pathname.startsWith(`${SCOPE.pathname}assets/`)) {
    event.respondWith(
      caches.match(req).then(
        (cached) =>
          cached ||
          fetch(req).then((res) => {
            if (res.ok) {
              const copy = res.clone();
              caches
                .open(ASSET_CACHE)
                .then((cache) => cache.put(req, copy))
                .then(trimAssets);
            }
            return res;
          }),
      ),
    );
  }
});
