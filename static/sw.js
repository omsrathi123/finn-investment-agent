// FINN service worker: makes the app installable and lets alerts surface as system notifications.
const SHELL = "finn-shell-v2";
self.addEventListener("install", e => { e.waitUntil(caches.open(SHELL).then(c => c.addAll(["/", "/static/icon.svg"]))); self.skipWaiting(); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== SHELL).map(k => caches.delete(k))))); self.clients.claim(); });
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin || u.pathname.startsWith("/api/")) return; // data is always live
  e.respondWith(fetch(e.request).then(r => { const copy = r.clone(); caches.open(SHELL).then(c => c.put(e.request, copy)); return r; })
    .catch(() => caches.match(e.request)));
});
// Ready for server-sent Web Push (VAPID) when a push service is added; today alerts come from in-app polling.
self.addEventListener("push", e => {
  const d = e.data ? e.data.json() : { title: "FINN", body: "New alert" };
  e.waitUntil(self.registration.showNotification(d.title, { body: d.body, tag: d.tag, icon: "/static/icon.svg" }));
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window" }).then(ws => ws.length ? ws[0].focus() : self.clients.openWindow("/")));
});
