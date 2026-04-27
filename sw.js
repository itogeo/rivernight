/**
 * sw.js — Selway River App Service Worker
 * 
 * Strategy:
 *  - App shell (HTML/JS/CSS/GeoJSON): Cache-first, update in background
 *  - Map tiles: Cache-first (once downloaded, never re-fetch on river)
 *  - USGS gauge: Network-first with stale fallback (ok to fail offline)
 */

const APP_VERSION = 'v1.7.0';
const SHELL_CACHE = `selway-shell-${APP_VERSION}`;
const TILE_CACHE  = `selway-tiles-${APP_VERSION}`;
const DATA_CACHE  = `selway-data-${APP_VERSION}`;

// App shell — always cached
const SHELL_ASSETS = [
  '/',
  '/index.html',
  '/app.js',
  '/manifest.json',
  '/data/river_centerline.geojson',
  '/data/pois.geojson',
  '/data/mfsalmon/river_centerline.geojson',
  '/data/mfsalmon/pois.geojson',
  '/data/mainsalmon/river_centerline.geojson',
  '/data/mainsalmon/pois.geojson',
  'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js',
  'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
];

// ---------------------------------------------------------------------------
// Install — pre-cache app shell
// ---------------------------------------------------------------------------
self.addEventListener('install', event => {
  console.log('[SW] Installing', APP_VERSION);
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then(cache => cache.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting())
  );
});

// ---------------------------------------------------------------------------
// Activate — clean old caches
// ---------------------------------------------------------------------------
self.addEventListener('activate', event => {
  console.log('[SW] Activating', APP_VERSION);
  const keep = [SHELL_CACHE, TILE_CACHE, DATA_CACHE];
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => !keep.includes(k)).map(k => {
          console.log('[SW] Deleting old cache:', k);
          return caches.delete(k);
        })
      )
    ).then(() => self.clients.claim())
  );
});

// ---------------------------------------------------------------------------
// Fetch — routing strategy
// ---------------------------------------------------------------------------
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Flow gauge APIs — network first, stale fallback, silent fail offline
  if (url.hostname === 'waterservices.usgs.gov' || url.hostname.includes('water.noaa.gov')) {
    event.respondWith(networkFirstWithFallback(event.request, DATA_CACHE));
    return;
  }

  // Map tiles — cache first (builds offline map as user browses)
  if (
    url.pathname.includes('/tiles/') ||
    url.hostname.includes('arcgisonline.com') ||
    url.hostname.includes('tile.openstreetmap.org') ||
    url.hostname.includes('opentopomap.org')
  ) {
    event.respondWith(cacheFirstTile(event.request));
    return;
  }

  // App shell — cache first
  event.respondWith(cacheFirst(event.request, SHELL_CACHE));
});

// ---------------------------------------------------------------------------
// Strategies
// ---------------------------------------------------------------------------

async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch {
    return new Response('Offline — resource not cached', { status: 503 });
  }
}

async function cacheFirstTile(request) {
  const cache = await caches.open(TILE_CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch {
    // Return a tiny transparent PNG when tile unavailable offline
    return new Response(OFFLINE_TILE_PNG, {
      headers: { 'Content-Type': 'image/png' }
    });
  }
}

async function networkFirstWithFallback(request, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request, { signal: AbortSignal.timeout(5000) });
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch {
    const cached = await cache.match(request);
    if (cached) return cached;
    return new Response(JSON.stringify({ error: 'offline', cached: false }), {
      status: 503,
      headers: { 'Content-Type': 'application/json' }
    });
  }
}

// ---------------------------------------------------------------------------
// Offline tile — 1x1 transparent PNG
// ---------------------------------------------------------------------------

const OFFLINE_TILE_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';

function b64ToUint8Array(b64) {
  const bStr = atob(b64);
  const arr = new Uint8Array(bStr.length);
  for (let i = 0; i < bStr.length; i++) arr[i] = bStr.charCodeAt(i);
  return arr;
}

const OFFLINE_TILE_PNG = b64ToUint8Array(OFFLINE_TILE_B64);

// ---------------------------------------------------------------------------
// Message handler — tile pre-caching progress
// ---------------------------------------------------------------------------
self.addEventListener('message', async event => {
  if (event.data?.type === 'PRECACHE_TILES') {
    const { urls } = event.data;
    const port = event.ports[0]; // MessageChannel port from app.js
    const cache = await caches.open(TILE_CACHE);
    let done = 0;

    for (const url of urls) {
      try {
        const cached = await cache.match(url);
        if (!cached) {
          const response = await fetch(url);
          if (response.ok) await cache.put(url, response);
        }
      } catch {}
      done++;
      if (port && done % 20 === 0) {
        port.postMessage({ type: 'PRECACHE_PROGRESS', done, total: urls.length });
      }
    }

    if (port) {
      port.postMessage({ type: 'PRECACHE_DONE', total: done });
    }
  }

  if (event.data?.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
