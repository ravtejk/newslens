/* ==========================================================================
   THE SERVICE WORKER — the paper opens on a train.
   NL-163 Stage-A M2.

   THE LAW IT IMPLEMENTS, stated before the code (adjudication Q3, addendum
   §§7/11):

     * THE CACHE IS NEVER AUTH-GATED. A dead session, an expired token, a host
       that answers 401 — none of them may take the last edition away from a
       reader who already has it. That is not a fallback here, it is the
       structure: an auth answer from the network is treated exactly like no
       answer at all, and the cached edition is served instead.
     * FIRST PAINT IS CONTENT, NEVER A SPINNER. When the cache already holds
       TODAY's edition, it is served immediately without waiting for a network
       that can only agree with it.
     * NO UPDATE THEATER. Nothing announces a new version, nothing swaps the
       page under a reader mid-sentence. A fresher edition is written to the
       cache and appears on the next open, which is how a paper works.
     * SAME-ORIGIN ONLY, VERSIONED. The cache holds this paper's own app
       assets and its own editions; a poisoned cache serves forever, so
       nothing else is admitted and the version below evicts everything on a
       bump.

   THE FRESHNESS RULE, and why it is not plain cache-first: a cache-first
   morning would serve YESTERDAY's paper to a reader standing in a live
   network with today's already on the host — the one failure this product
   cannot have. So the cache wins only when it holds today's edition; any
   other day, the press is consulted first and the cache catches the fall.
   ========================================================================== */

var VERSION = 'newslens-v1';
var EDITION_KEY = '/__newslens_edition__';   /* the "last edition" slot */
var NET_TIMEOUT_MS = 2500;

var SHELL_ASSETS = [
  '/static/tokens.css',
  '/static/shell.css',
  '/static/shell.js',
  '/static/boot.js',
  '/static/manifest.webmanifest',
  '/static/apple-touch-icon.png',
  '/static/favicon.png',
  '/static/icon-192.png',
  '/static/icon-512.png'
];

self.addEventListener('install', function (event) {
  event.waitUntil(
    caches.open(VERSION).then(function (cache) {
      return cache.addAll(SHELL_ASSETS);
    }).then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (k) {
        return k === VERSION ? null : caches.delete(k);
      }));
    }).then(function () { return self.clients.claim(); })
     .then(function () { return warmEdition(); })
  );
});

/* THE FIRST MORNING. A worker installs DURING a page load it did not
   intercept — it claims the page afterwards — so without this, the reader's
   very first visit would leave the cache holding the app shell and no
   edition, and a train journey that same evening would open nothing. Measured
   on a real browser before it was written: load, kill the network, reload,
   Chrome's error page.

   So the worker fetches the front page once, on its own, as soon as it is in
   charge. The request carries the session like any same-origin fetch, and an
   answer that is not an edition (a login redirect, a 401, no network) is
   simply not cached — this is a warm-up, never a retry loop. */
function warmEdition() {
  return caches.open(VERSION).then(function (cache) {
    return cache.match(EDITION_KEY);
  }).then(function (already) {
    if (already) { return null; }
    return fetch('/').then(function (response) {
      if (!isRefusal(response)) { storeEdition('/', response); }
    }).catch(function () { /* offline at install: the next open warms it */ });
  });
}

function isEditionPath(path) {
  return path === '/' || /^\/editions\/\d{4}-\d{2}-\d{2}$/.test(path);
}

function todayLocal() {
  var d = new Date();
  return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) +
    '-' + ('0' + d.getDate()).slice(-2);
}

/* An edition response, or nothing.

   THE SLOT ANSWERS ONLY THE FRONT PAGE. `/` means "the paper", so the newest
   edition on the device is the right answer to it; `/editions/2026-08-12`
   names one specific day, and answering it with a different day's paper would
   be a quiet substitution the reader cannot see. A date this device has never
   received simply is not here. */
function cachedEdition(request, allowSlot) {
  return caches.open(VERSION).then(function (cache) {
    return cache.match(request).then(function (hit) {
      if (hit || !allowSlot) { return hit; }
      return cache.match(EDITION_KEY);
    });
  });
}

function storeEdition(request, response) {
  if (!response || response.status !== 200) { return; }
  var date = response.headers.get('X-NewsLens-Edition-Date');
  if (!date) { return; }
  var forUrl = response.clone(), forSlot = response.clone();
  return caches.open(VERSION).then(function (cache) {
    cache.put(request, forUrl);
    /* THE SLOT HOLDS THE NEWEST EDITION THIS DEVICE HAS EVER RECEIVED, not
       the last one browsed. Reading Tuesday's edition out of the archive on
       Friday night must not cost the reader Friday's paper on Saturday's
       train — which is exactly what a last-write slot would have done. */
    return cache.match(EDITION_KEY).then(function (held) {
      var heldDate = held && held.headers.get('X-NewsLens-Edition-Date');
      if (!heldDate || heldDate <= date) { return cache.put(EDITION_KEY, forSlot); }
    });
  });
}

/* An answer from the network that is NOT this reader's edition: a redirect to
   the login page, a 401/403, a 5xx. Each of them means "the network cannot
   give you the paper right now", and the cache answers all three the same
   way. This is the auth-gating law in one function. */
function isRefusal(response) {
  if (!response) { return true; }
  if (response.status === 401 || response.status === 403) { return true; }
  if (response.status >= 500) { return true; }
  if (response.redirected) {
    try { return new URL(response.url).pathname === '/login'; }
    catch (e) { return false; }
  }
  return false;
}

function networkFirst(request, allowSlot) {
  var network = new Promise(function (resolve) {
    var settled = false;
    var timer = setTimeout(function () {
      if (!settled) { settled = true; resolve(null); }
    }, NET_TIMEOUT_MS);
    fetch(request).then(function (response) {
      if (settled) { return; }
      settled = true; clearTimeout(timer); resolve(response);
    }).catch(function () {
      if (settled) { return; }
      settled = true; clearTimeout(timer); resolve(null);
    });
  });

  return network.then(function (response) {
    if (!isRefusal(response)) {
      storeEdition(request, response);
      return response;
    }
    return cachedEdition(request, allowSlot).then(function (hit) {
      /* THE CACHE OUTRANKS THE REFUSAL. A reader offline at 6am, or one whose
         session died overnight, still opens the paper. */
      return hit || response || Response.error();
    });
  });
}

self.addEventListener('fetch', function (event) {
  var request = event.request;
  if (request.method !== 'GET') { return; }
  var url;
  try { url = new URL(request.url); } catch (e) { return; }
  if (url.origin !== self.location.origin) { return; }

  /* The liveness probe must always meet the real network — it is how the page
     learns it is offline. Caching it would make the stamp lie. */
  if (url.pathname.indexOf('/api/') === 0) { return; }

  /* THE ARCHIVE IS NEVER CACHED and never answered from here: it is a live
     index of what the PRESS holds, and a stale one would offer dates this
     device cannot open. Offline, the shell removes its door instead of
     leaving one that goes nowhere (shell.js) — presence is the signal. */
  if (isEditionPath(url.pathname)) {
    var isFront = url.pathname === '/';
    event.respondWith(
      cachedEdition(request, isFront).then(function (hit) {
        var isToday = hit && hit.headers.get('X-NewsLens-Edition-Date') === todayLocal();
        if (isToday && isFront) {
          /* Today's paper is already here. Serve it now; refresh the cache
             quietly behind the reader, and say nothing about it. */
          event.waitUntil(fetch(request).then(function (fresh) {
            if (!isRefusal(fresh)) { storeEdition(request, fresh); }
          }).catch(function () {}));
          return hit;
        }
        return networkFirst(request, isFront);
      })
    );
    return;
  }

  if (url.pathname.indexOf('/static/') === 0 || url.pathname === '/sw.js') {
    event.respondWith(
      caches.match(request).then(function (hit) {
        return hit || fetch(request).then(function (response) {
          if (response && response.status === 200) {
            var copy = response.clone();
            caches.open(VERSION).then(function (c) { c.put(request, copy); });
          }
          return response;
        });
      })
    );
  }
});
