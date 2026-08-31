/* ==========================================================================
   THE SHELL SCRIPT — what the host adds to a frozen edition, and nothing more.
   NL-163 Stage-A M2.

   Runs on two kinds of page: the shell states this service renders (login,
   no-edition, archive, 404) and a SERVED EDITION, where it arrives as the one
   injected tail (hosted/app.py::augment_edition). It does exactly three things:

     1. registers the service worker (the offline morning);
     2. fills M1's empty offline slot with the §7 stamp — the bundle cannot
        know how it arrived, and neither can the host; only the client can;
     3. makes the archive reachable from an edition (standalone mode has no
        URL bar and no back button — §6).

   IT NO LONGER TOUCHES THE LOGIN PAGE (M3). In M2 it flipped the
   passkey/password arms, because that toggle was the whole of the login
   page's behaviour. M3 gave that page a real ceremony, and the ceremony went
   into its own file (/static/login.js) for one reason: THIS script is injected
   into every served edition, and no line of sign-in behaviour belongs inside a
   document a reader opens on a train.

   READ-PURE HOLDS. Nothing here writes: no follow verb, no POST, no event
   beacon. The read ledger is a host-side observation of GETs (Q6) precisely so
   that no inbound write channel exists.
   ========================================================================== */
(function () {
  'use strict';

  /* -- 1. the service worker ------------------------------------------- */
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('/sw.js', { scope: '/' })
        .catch(function () { /* an unregistered worker costs the cache, not the paper */ });
    });
  }

  /* -- the edition island ---------------------------------------------- */
  var node = document.getElementById('newslens-edition');
  if (!node) { return; }          /* a shell page: nothing below applies */
  var island;
  try { island = JSON.parse(node.textContent || '{}'); } catch (e) { return; }
  if (!island || !island.date) { return; }

  /* -- 3. the archive door --------------------------------------------- */
  /* The bundle's section line carries ONE word — correct for a document that
     is one view (M1). Served by a host that also holds an archive, the same
     line must carry the way there: standalone mode has no browser chrome, so
     a destination with no on-page door is a destination that does not exist
     (§6). Added here rather than baked into the bundle because it is the
     HOST's fact, not the edition's: an artifact opened from disk has no
     archive to offer.

     AND IT CLOSES WHEN THE PRESS IS UNREACHABLE. The archive is a live index
     of what the host holds — it is deliberately never cached, because a
     stale one would list dates this device cannot open. So offline the door
     goes rather than leading nowhere: presence is the signal (§1's
     honest-quiet pattern), not a link that fails when tapped. */
  function archiveDoor(open) {
    var nav = document.querySelector('.section-line .page');
    if (!nav) { return; }
    var link = nav.querySelector('a[data-shell-archive]');
    if (!open) {
      if (link) { link.parentNode.removeChild(link); }
      return;
    }
    if (link) { return; }
    link = document.createElement('a');
    link.href = '/archive';
    link.textContent = 'Archive';
    link.setAttribute('data-shell-archive', '1');
    /* Inline because an edition carries its own stylesheet and never loads
       shell.css; these are the shipped .section-current geometry values. */
    link.style.display = 'inline-block';
    link.style.padding = '0.35rem 0';
    link.style.marginRight = '1.1rem';
    nav.appendChild(link);
  }
  /* Optimistic, except when the device already knows it is offline: airplane
     mode is the common offline open, and drawing a door there just to take it
     away a second later would be the theater §11 rules out. */
  archiveDoor(navigator.onLine !== false);

  /* -- 2. the offline stamp (§7 grammar, exactly) ----------------------- */
  var slot = document.getElementById('offline-stamp');
  if (!slot) { return; }

  function hhmm(iso) {
    /* UTC, deliberately: the line sits directly under "Edition assembled
       06:02 UTC", the two clocks are minutes apart on a healthy morning, and
       a local-time render would put two different clocks in one block with
       only one of them labelled. */
    var m = /T(\d{2}):(\d{2})/.exec(String(iso || ''));
    return m ? (m[1] + ':' + m[2]) : '';
  }

  function localToday() {
    var d = new Date();
    return d.getFullYear() + '-' +
      ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2);
  }

  function showStamp() {
    var at = hhmm(island.pushed_at);
    var strong = document.createElement('strong');
    strong.textContent = 'Offline';
    slot.textContent = '';
    slot.appendChild(strong);
    /* THE RULED COPY, both forms (§7):
         cached edition IS today's ->  Offline · pushed at HH:MM
         cached edition is older   ->  Offline · <Day>'s edition · pushed at HH:MM
       Never a banner, never sticky, never danger ink: the dateline already
       says what day it is; this line says why that is the day on screen. */
    var stamp = at ? ' · pushed at ' + at : '';      /* no receipt, no clause */
    var rest = (island.date === localToday())
      ? stamp
      : ' · ' + island.day + '’s edition' + stamp;
    if (rest) { slot.appendChild(document.createTextNode(rest)); }
    slot.hidden = false;
    archiveDoor(false);          /* the index lives on the press */
  }

  function probe() {
    if (navigator.onLine === false) { showStamp(); return; }
    var done = false;
    var timer = setTimeout(function () {
      if (!done) { done = true; showStamp(); }
    }, 3000);
    fetch('/api/ping', { cache: 'no-store' }).then(function (r) {
      if (done) { return; }
      done = true; clearTimeout(timer);
      if (!r.ok) { showStamp(); }
    }).catch(function () {
      if (done) { return; }
      done = true; clearTimeout(timer);
      showStamp();
    });
  }

  probe();
  /* A morning that starts offline and finds the network mid-read: the stamp
     goes quiet, because it has stopped being true. No banner appears in the
     other direction either — no update theater (§11). */
  window.addEventListener('online', function () {
    slot.hidden = true;
    archiveDoor(true);
  });
  window.addEventListener('offline', showStamp);
})();
