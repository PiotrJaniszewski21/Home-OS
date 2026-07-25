const CACHE_VERSION = 12;
const CACHE_NAME = 'homeos-v' + CACHE_VERSION;

self.addEventListener('install', (e) => {
    self.skipWaiting();
    e.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.map(k => caches.delete(k)))
        )
    );
});

self.addEventListener('activate', (e) => {
    e.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.map(k => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (e) => {
    // Pass everything through to network, no caching
    return;
});
