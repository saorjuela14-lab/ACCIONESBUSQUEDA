// Self-destructing service worker — clears all caches and unregisters.
// Previous versions cached JS/CSS and blocked logout/login fixes from appearing.
const CACHE_PREFIX = "monarch-";

self.addEventListener("install", (e) => {
  e.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.map((k) => caches.delete(k))))
      .then(() => self.registration.unregister())
      .then(() => self.clients.claim())
  );
});

// Never intercept fetches — network only
self.addEventListener("fetch", () => {});
