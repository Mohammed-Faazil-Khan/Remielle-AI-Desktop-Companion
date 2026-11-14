// Keeps her page, her model and her icons on the phone, so opening the home-screen app does not
// wait for the PC to wake up. Anything that involves talking - the microphone, her voice, the live
// game - always goes to the computer and is never answered from the cache.
const VERSION = 'remielle-v2';
const SHELL = [
    '/',
    '/static/manifest.webmanifest',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
    '/static/icons/apple-touch-icon.png',
    '/static/lib/three.module.js',
    '/static/lib/loaders/GLTFLoader.js',
    '/static/lib/utils/BufferGeometryUtils.js',
    '/static/lib/three-vrm.module.js',
    '/static/character.vrm'
];

self.addEventListener('install', (event) => {
    event.waitUntil((async () => {
        const cache = await caches.open(VERSION);
        // One missing file (a background image you have not added) must not abort the install.
        await Promise.allSettled(SHELL.map((path) => cache.add(new Request(path, { cache: 'reload' }))));
        await self.skipWaiting();
    })());
});

self.addEventListener('activate', (event) => {
    event.waitUntil((async () => {
        const keys = await caches.keys();
        await Promise.all(keys.filter((key) => key !== VERSION).map((key) => caches.delete(key)));
        await self.clients.claim();
    })());
});

self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.method !== 'GET') return;

    const url = new URL(request.url);
    if (url.origin !== self.location.origin) return;
    if (url.pathname === '/speak' || url.pathname === '/ws' || url.pathname === '/api') return;

    const key = new Request(url.pathname);   // ?token=... must not make a second copy of everything
    event.respondWith((async () => {
        const cache = await caches.open(VERSION);
        const cached = await cache.match(key);
        const fresh = fetch(request)
            .then((answer) => {
                if (answer && (answer.ok || answer.status === 206)) cache.put(key, answer.clone());
                return answer;
            })
            .catch(() => null);

        if (url.pathname.startsWith('/static/')) return cached || (await fresh) || Response.error();
        return (await fresh) || cached || Response.error();
    })());
});
