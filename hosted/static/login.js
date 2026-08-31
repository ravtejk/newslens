/* ==========================================================================
   THE CEREMONY — the login page's whole behaviour, and it lives ONLY here.
   NL-163 Stage-A M3 (the principal's ruling 2026-08-31: Stytch).

   THIS FILE IS OURS. The vendor's script is loaded beside it on this one page
   and is reached through the ~30-line adapter at the bottom — deliberately
   small, deliberately the only place a vendor name appears in behaviour, so
   that the recorded falsifier (if live-mode passkeys turn out gated, Clerk
   re-enters at his eye) costs one function rather than a page.

   WHAT IT DOES, in the order it does it:

     1. THE SILENT RETURN. A device that is still signed in with the vendor
        but whose short-lived proof has expired is put straight back where it
        was going. The reader types nothing and sees no prompt — this is the
        mechanism behind "credentials once per device" (§9), and it is why the
        vendor's five-minute JWT never becomes a five-minute login.
     2. THE PASSKEY ARM (Face ID), then the password arm behind the quiet link.
     3. THE EXCHANGE. Whatever the ceremony returns is handed to this paper's
        own /api/session, which verifies it against the vendor's public key and
        sets ONE HttpOnly cookie. Nothing in this file ever reads that cookie —
        nothing can.
     4. THE TWO FAILURE REGISTERS (§8). A refused credential gets ONE quiet
        inline line in danger ink. Anything that is the machine's fault rather
        than the reader's — no network, a door with no keys in it — gets the
        plain-ink unreachable grammar instead, because unreachability is a
        fact, not a fault.

   WHAT IT NEVER DOES: store a password, store a token, log either, show a
   spinner, shake a field, or announce anything on first paint.
   ========================================================================== */
(function () {
  'use strict';

  var node = document.getElementById('newslens-login');
  if (!node) { return; }
  var cfg;
  try { cfg = JSON.parse(node.textContent || '{}'); } catch (e) { return; }

  var els = {
    error: document.getElementById('login-error'),
    unreach: document.getElementById('login-unreach'),
    why: document.getElementById('login-unreach-why'),
    passkeyArm: document.getElementById('login-passkey'),
    passwordArm: document.getElementById('login-password'),
    email: document.getElementById('li-email'),
    email2: document.getElementById('li-email2'),
    password: document.getElementById('li-pw')
  };

  /* -- the two registers, §8 -------------------------------------------- */

  function quiet() {
    /* Every attempt starts from silence. A stale refusal sitting above a new
       attempt would be the page lying about what just happened. */
    [els.error, els.unreach, els.why].forEach(function (el) {
      if (el) { el.hidden = true; el.textContent = ''; }
    });
  }

  /* THE ONE SCOPED --danger EXTENSION (addendum §8): one quiet inline line,
     no banner, no shake, and only ever after a real attempt. */
  function refused(sentence) {
    if (!els.error) { return; }
    els.error.textContent = sentence;
    els.error.hidden = false;
  }

  /* The unreachable grammar, in plain ink: full fact + quiet why. */
  function unreachable(fact, why) {
    if (els.unreach) { els.unreach.textContent = fact; els.unreach.hidden = false; }
    if (els.why && why) { els.why.textContent = why; els.why.hidden = false; }
  }

  /* -- the version floor (M4 gate FIX-3) --------------------------------- */

  /* WHY THIS EXISTS AT ALL. The vendor's loader URL carries no version, so the
     artifact behind it can change shape on any morning without anything here
     changing; the CSP origin pin can see WHERE the script came from and never
     WHAT SHAPE it has. And a missing method degrades INVISIBLY in both places
     it matters: the password arm throws a TypeError, which is the same error
     class a dead network throws, so the page used to answer a version problem
     with "Sign-in needs the network" — loud, and a lie about the cause; and
     the silent return simply stays silent, which is indistinguishable from an
     ordinary first morning. The no-silent-catches law reaches both. This is
     the only tripwire the product can hold, so it holds it.

     WHAT IT MAY NEVER DO: block an arm. A false positive here can add a line
     and nothing else — an arm whose methods are all present keeps working
     whatever the inventory says about the others. */
  var NEEDED = [['webauthn', 'authenticate'], ['passwords', 'authenticate'],
                ['session', 'authenticate'], ['session', 'getTokens']];
  /* Filled the first time a REAL client is built, and never over the fixture
     seam — the inventory is a statement about the vendor's artifact. */
  var missing = [];

  function inventory(client) {
    var out = [];
    NEEDED.forEach(function (path) {
      var product = client && client[path[0]];
      if (!product || typeof product[path[1]] !== 'function') {
        out.push(path[0] + '.' + path[1]);
      }
    });
    return out;
  }

  /* PLAIN INK, NOT DANGER INK: an unexpected script is the machine's fault,
     not the reader's — the same register as an unreachable press (§8). */
  function versionNotice() {
    unreachable('This page loaded an unexpected version of its sign-in service.',
                'Signing in may not work or may not last; the paper’s operator '
                + 'has the fix in the runbook.');
  }

  function noteVersion() {
    if (missing.length) { versionNotice(); }
  }

  /* -- the exchange ------------------------------------------------------ */

  function exchange(jwt) {
    return fetch('/api/session', {
      method: 'POST',
      /* The same-origin header this paper's endpoint requires. A cross-site
         form cannot set it, so nobody can plant a session in this browser. */
      headers: { 'Content-Type': 'application/json', 'X-NewsLens-Session': '1' },
      body: JSON.stringify({ session_jwt: jwt, next: cfg.next || '/' }),
      credentials: 'same-origin',
      cache: 'no-store'
    }).then(function (resp) {
      if (!resp.ok) { return resp.json().catch(function () { return {}; })
        .then(function () { throw { kind: 'refused' }; }); }
      return resp.json();
    }).then(function (data) {
      /* replace(), not assign(): the login page must not become a stop on the
         back path of a reader who is now signed in. */
      window.location.replace((data && data.next) || '/');
    });
  }

  /* -- failure classification ------------------------------------------- */

  /* A CANCELLED PASSKEY PROMPT IS NOT A FAILURE. Dismissing Face ID is a
     decision, and answering a decision with danger ink would be the product
     scolding a reader who did nothing wrong. */
  function isCancellation(err) {
    var name = (err && (err.name || err.error_type)) || '';
    return name === 'NotAllowedError' || name === 'AbortError';
  }

  function isUnreachable(err) {
    if (!err) { return false; }
    if (err.kind === 'unconfigured' || err.kind === 'offline') { return true; }
    /* fetch() rejects with a TypeError when the network is gone; the vendor's
       client surfaces the same failure through its own transport. */
    return (err.name === 'TypeError') || navigator.onLine === false;
  }

  function report(err, member) {
    if (isCancellation(err)) { return; }
    if (err && err.kind === 'unconfigured') {
      unreachable('The press can’t be reached from here.',
                  'This paper’s door has not been given its keys yet.');
      return;
    }
    /* ATTRIBUTION BEFORE THE NETWORK GRAMMAR (FIX-3c). A missing method and a
       dead network are the same JS error class, and the network sentence was
       winning on both — sending the operator to watch a venue where nothing
       would ever appear. When the inventory names the very member THIS arm
       needed, the version line is the true one, and it is printed instead. */
    if (err && err.name === 'TypeError' && member
        && missing.indexOf(member) >= 0) {
      versionNotice();
      return;
    }
    if (isUnreachable(err)) {
      unreachable('The press can’t be reached from here.',
                  'Sign-in needs the network; the last edition on this device '
                  + 'opens without it.');
      return;
    }
    refused('That sign-in wasn’t accepted.');
  }

  /* -- the arms ---------------------------------------------------------- */

  function attempt(run, member) {
    quiet();
    return Promise.resolve()
      .then(run)
      .then(function (result) {
        /* THE ATTEMPT IS THE MOMENT THE FLOOR SPEAKS — never first paint. A
           reader who has not touched anything gets the drawn page in silence
           (§11); a reader who just asked to sign in is owed the fact. */
        noteVersion();
        var jwt = result && result.session_jwt;
        if (!jwt) { throw { kind: 'refused' }; }
        return exchange(jwt);
      }, function (err) {
        noteVersion();
        throw err;
      })
      .catch(function (err) { report(err, member); });
  }

  Array.prototype.forEach.call(
    document.querySelectorAll('[data-login-show]'), function (btn) {
      btn.addEventListener('click', function () {
        /* Switching arms clears the last attempt's line: it was about the
           other arm. */
        quiet();
        var want = btn.getAttribute('data-login-show');
        if (!els.passkeyArm || !els.passwordArm) { return; }
        els.passkeyArm.hidden = (want !== 'passkey');
        els.passwordArm.hidden = (want !== 'password');
      });
    });

  Array.prototype.forEach.call(
    document.querySelectorAll('[data-login-arm]'), function (btn) {
      btn.addEventListener('click', function () {
        var arm = btn.getAttribute('data-login-arm');
        if (arm === 'passkey') {
          /* Called straight out of the click handler: the WebAuthn prompt is
             only allowed to open inside a user gesture. */
          attempt(function () { return vendor().passkey(); },
                  'webauthn.authenticate');
        } else {
          attempt(function () {
            return vendor().password(
              (els.email2 && els.email2.value) || '',
              (els.password && els.password.value) || '');
          }, 'passwords.authenticate');
        }
      });
    });

  /* -- 1. the silent return --------------------------------------------- */

  /* Tried once, on load, and it says NOTHING when it fails: a device that has
     never signed in here has no session to refresh, and that is the ordinary
     first morning, not an error. */
  (function silentReturn() {
    var client;
    try { client = vendor(); } catch (e) { return; }
    if (!client.canRefresh || !client.canRefresh()) { return; }
    Promise.resolve().then(function () { return client.refresh(); })
      .then(function (result) {
        var jwt = result && result.session_jwt;
        if (jwt) { return exchange(jwt); }
      })
      .catch(function () { /* the arms above are already the answer */ });
  })();

  /* ======================================================================
     THE VENDOR ADAPTER — every vendor-shaped call in the product, in one
     place, in about thirty lines.

     Method names and parameters are the vendor's documented frontend SDK
     surface (docs read 2026-08-31). `session_duration_minutes` is passed on
     EVERY call on purpose: the vendor's rule is that "a successful
     authentication will continue to extend the session this many minutes",
     so the rolling ≥90-day law of §9 is held by passing the maximum
     (527,040 = 366 days) at every ceremony and at every silent refresh.

     PROTOTYPE: faked — `window.__NEWSLENS_AUTH__` replaces this whole object
     when present. That seam is how the suite and the design walk drive every
     state of this page with no vendor account and no network; it is checked
     FIRST so a stub is unambiguous, and it is inert in production because
     nothing sets it. See tests/test_nl163_auth.py and the M3 report's walk.
     ====================================================================== */
  function vendor() {
    if (window.__NEWSLENS_AUTH__) { return window.__NEWSLENS_AUTH__; }
    if (!cfg.ready) { throw { kind: 'unconfigured' }; }
    var client = window.__stytchClient;
    if (!client) {
      var factory = window.Stytch || window.StytchHeadlessClient;
      if (typeof factory !== 'function') { throw { kind: 'unconfigured' }; }
      try { client = factory(cfg.public_token); }
      catch (e) { client = new factory(cfg.public_token); }
      window.__stytchClient = client;
    }
    /* THE INVENTORY IS TAKEN HERE AND ONLY HERE — over the VENDOR's client.
       The fixture seam returned three lines up, so a stub can never be
       reported as a version problem, and the suite's own states stay clean. */
    missing = inventory(client);
    var minutes = cfg.session_minutes;
    return {
      passkey: function () {
        return client.webauthn.authenticate({ session_duration_minutes: minutes });
      },
      password: function (email, password) {
        return client.passwords.authenticate({
          email: email, password: password, session_duration_minutes: minutes });
      },
      canRefresh: function () {
        try { return !!(client.session.getTokens && client.session.getTokens()); }
        catch (e) { return false; }
      },
      refresh: function () {
        return client.session.authenticate({ session_duration_minutes: minutes });
      }
    };
  }
})();
