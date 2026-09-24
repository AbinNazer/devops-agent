const CACHE = 'jarvis-shell-v35';
const PRECACHE = [
  '/',
  '/static/boot.js?v=32',
  '/static/app.css?v=34',
  '/static/app.js?v=34',
  '/static/manifest.webmanifest',
  '/static/icon-192.svg',
  '/static/icon-512.svg',
  // Terminal shell assets are cached so an installed PWA can render the
  // terminal page (and report connection errors) before the network answers.
  '/static/terminal.js?v=34',
  '/static/vendor/xterm/xterm.min.js?v=5.5.0',
  '/static/vendor/xterm/xterm.min.css?v=5.5.0',
  '/static/vendor/xterm/addon-fit.min.js?v=0.10.0',
  '/static/vendor/xterm/addon-web-links.min.js?v=0.11.0'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key)));
    await self.clients.claim();
  })());
});

async function staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);
  const network = fetch(request).then(response => {
    if (response && response.ok) cache.put(request, response.clone());
    return response;
  }).catch(() => cached || new Response('Offline and not cached', {
    status: 503,
    statusText: 'Offline',
    headers: { 'Content-Type': 'text/plain' }
  }));
  return cached || network;
}

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  // Never touch another origin, the API, the WebSocket handshake, or the
  // terminal page: those must always hit the network so authentication and
  // upgrade handling stay server-controlled.
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/ws/')) return;
  if (url.pathname === '/terminal') return;

  // stale-while-revalidate everywhere, including /static/. A cache-first
  // strategy pinned stale JS in installed PWAs across deployments (the only
  // escape was bumping every ?v= query by hand), which made a home-screen app
  // behave differently from a freshly loaded tab.
  event.respondWith(staleWhileRevalidate(request));
});
