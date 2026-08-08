const CACHE = "monarch-v7-boot-splash";
const ASSETS = [
  "/dashboard/static/styles.css",
  "/dashboard/static/reliability.js",
  "/dashboard/static/voice.js",
  "/dashboard/static/app.js",
  "/dashboard/static/icon.png",
  "/dashboard/static/assets/mark.png",
  "/dashboard/static/assets/logo.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  const url = new URL(e.request.url);

  // Never cache HTML entry points — login must always be server-gated
  if (
    url.pathname === "/" ||
    url.pathname === "/login" ||
    url.pathname === "/dashboard" ||
    url.pathname.startsWith("/api/")
  ) {
    return; // network only (default browser fetch)
  }

  // JS/CSS: network first so auth/login fixes deploy immediately
  if (url.pathname.includes("/dashboard/static/") && /\.(js|css)$/.test(url.pathname)) {
    e.respondWith(
      fetch(e.request)
        .then((res) => {
          const clone = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, clone));
          return res;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // Static assets only
  if (url.pathname.startsWith("/dashboard/static/")) {
    e.respondWith(
      caches.match(e.request).then((cached) => cached || fetch(e.request))
    );
  }
});
