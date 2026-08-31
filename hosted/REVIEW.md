# Security review packet — the NewsLens hosted reader

**For an independent engineer reviewing this before it goes on the public
internet.** You are the first human to look at this code. Everything here was
written and reviewed by AI agents; that review was real, and it is also the
reason you were hired — model-reviewing-model shares its blind spots, and
agreeable, plausible-looking wrongness is exactly the failure mode your eyes
are for.

This document is a map, not a defence. Where we think we are weak, it says so
first.

**Estimated review time: 4–8 hours.** §0 tells you what to read and in what
order if you have less.

Written 2026-08-31 against the tree at the commit this file ships in. Every
`file:line` was generated mechanically at those bytes — but line numbers drift,
so treat them as pointers and re-check before trusting one.

---

## 0. Scope — what you are being asked to review, and what you are not

**In scope: three things.**

| | What it is | Where | Size |
|---|---|---|---|
| 1 | **The hosted reader** — a small Flask service that serves a pre-rendered HTML document to a signed-in reader | `hosted/app.py`, `hosted/webhelpers.py`, `hosted/hoststore.py` | ~1,400 lines |
| 2 | **The publish path** — a token-authenticated `PUT` that accepts a day's edition from the author's laptop | `hosted/app.py:661`, `hosted/hoststore.py` | ~200 lines |
| 3 | **The auth integration** — session-JWT verification against a third-party identity provider (Stytch), plus the browser-side sign-in ceremony | `hosted/sessionauth.py`, `hosted/static/login.js` | ~690 lines |

**Why `app.py` is one long file, since you will notice.** It is 773 lines, and
that is deliberate rather than neglected. It holds the credential surface in
the order a request meets it: the boot refusal, configuration, the one function
that touches edition bytes, then `create_app` with `current_user`, the policy
and all ten routes. The leaf helpers that are pure functions of their arguments
were split out to `webhelpers.py`; we stopped there on purpose, because the
next cut anyone would reach for — moving the boot refusal into its own module —
would put §6's two guards, the boot-time one and the request-time one, in two
different files, and those are the pair you most want to read together. If you
would still rather it were carved up, say so in your report; we would rather
you could read the door in one pass.

**Explicitly out of scope.** The much larger application in `src/newslens/`
(the news pipeline that generates the editions) is a **local-only, single-user
tool that never faces the internet**. It has its own review document,
`PREFLIGHT.md`, and nothing in it is reachable from the hosted service. The two
programs share a repository and share no process, no port and no database. If
you want the boundary in one sentence: **the laptop pushes bytes up; nothing
comes back down.**

**The threat model we designed against.** A public HTTPS endpoint on a rented
box. Anonymous internet attackers; hostile web pages in the reader's browser; a
hostile client presenting forged credentials; a compromised host disclosing
what it stores. **Not** in the model: a malicious operator (he owns the data),
or a nation-state with the identity provider's signing key.

**If you only have two hours,** read in this order and stop wherever you run
out:

1. §3 the auth decision points (the credential surface — this is the review)
2. §4 the publish path (the only route that accepts data from outside)
3. §6 the development bypass (the thing most likely to end badly)
4. §8 what we think is weakest — including a vendor-SDK mismatch we found and
   deliberately did **not** repair, and the three defects our own review found
   and did (§3.6, §3.7, §4 check 7)

---

## 1. Fifteen minutes to a running instance

You do not need an account with anyone, or any key, to run and attack this.

```sh
cd <repo root>
python3 -m venv .venv && .venv/bin/pip install -r hosted/requirements.txt
mkdir -p /tmp/nl-review

# a push token and the digest the host stores
TOK=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
DIG=$(python3 -c "import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" "$TOK")

NEWSLENS_HOSTED_DATA=/tmp/nl-review \
NEWSLENS_DEV_NO_AUTH=1 \
NEWSLENS_USER_STREAMS='{"local":"main"}' \
NEWSLENS_PUSH_TOKENS="{\"main\":\"$DIG\"}" \
.venv/bin/python -m hosted.app --host 127.0.0.1 --port 5473
```

`NEWSLENS_DEV_NO_AUTH=1` is the one faked seam (§5) — it skips session
verification so you can drive the app without an identity provider. **It
refuses to boot on any non-loopback bind and 403s any non-loopback caller even
when it does boot** (§6); try to defeat that, we would like to know.

To exercise the *real* lock instead, the test suite mints its own RSA keypair
and JWKS and never opens a socket — `tests/test_nl163_auth.py` is a working
attack harness you can extend. Run the whole suite with
`.venv/bin/python -m pytest -q` (about 7 minutes, no network, no keys).

---

## 2. The shape of the thing

```
   the author's laptop                      the rented box
  ┌────────────────────┐                  ┌──────────────────────────┐
  │ generates an       │  PUT + bearer    │ /api/streams/<s>/        │
  │ edition, freezes   │ ───────────────► │   editions/<date>        │
  │ it to one HTML doc │  (token → digest │      ↓ verify sha256     │
  │                    │   compare)       │   file on a volume       │
  └────────────────────┘                  │      ↓                   │
                                          │ GET / → the same bytes,  │
   the reader's phone                     │   plus a small tail      │
  ┌────────────────────┐  cookie (vendor  │      ↑                   │
  │ signs in once,     │  JWT) verified   │ session cookie verified  │
  │ reads, works       │ ───────────────► │ against cached JWKS      │
  │ offline            │  every request   └──────────────────────────┘
  └────────────────────┘
```

Three properties worth holding onto, because most of the design follows
from them:

* **The host renders nothing.** It stores a finished HTML document and hands it
  back. There is no template engine touching edition content, so the usual
  injection surface of a news site is not present here — the document was built
  on the laptop and is served verbatim. The one exception is the small
  augmentation in `augment_edition` (`app.py:328`), reviewed in §4.
* **The host holds no vendor secret, structurally.** It can verify a signature
  and nothing else — it cannot create an account, mint a session, or revoke
  one. That is why putting it on a rented box is a small decision. See §3.4.
* **The host holds no product data.** No stories, no reading history beyond a
  minimal ledger, no personal data other than an opaque user id. Losing the
  whole volume is a non-event: every edition is re-pushable from the laptop.

---

## 3. The auth decision points — read this section twice

Every place a request's identity is decided.

### 3.1 The routes, and what each requires

Generated from the live URL map at these bytes:

| Route | Method | Auth required | Handler |
|---|---|---|---|
| `/` | GET | **session** | `app.py:538` |
| `/editions/<date>` | GET | **session** | `app.py:559` |
| `/archive` | GET | **session** | `app.py:571` |
| `/api/session` | **POST** | none (it *creates* the session) | `app.py:605` |
| `/api/streams/<stream>/editions/<date>` | **PUT** | **bearer token** | `app.py:661` |
| `/login` | GET | none, deliberately | `app.py:582` |
| `/sw.js` | GET | none, deliberately | `app.py:733` |
| `/api/ping` | GET | none | `app.py:722` |
| `/healthz` | GET | none | `app.py:729` |
| `/static/<path>` | GET | none | Flask builtin |

**There are exactly two non-GET routes in the entire service**, and a test
asserts that by parsing every module in `hosted/` and comparing the set
(`test_there_is_no_signup_surface_anywhere_in_the_service`). If you add a
route, that test fails — deliberately.

The four unauthenticated GETs are unauthenticated on purpose: `/login` is the
door (a reader with an expired session must reach it with no session at all),
`/sw.js` and `/static` must load before any session exists or the offline mode
breaks, and `/healthz` and `/api/ping` return one boolean each.

**What we want you to check:** that no authenticated route can be reached
without a session, and that a refused request leaks no edition bytes. Our own
harness drives all of these; yours will be better.

### 3.2 The reading path

`current_user()` (`app.py:380`) is the whole of it, and it is short on purpose:
read one cookie, verify it locally against a cached public key, return a user
id or `None`. There is no vendor round trip on the reading path.

`reader()` (`app.py:415`) then maps user → stream via a configured
`{user_id: stream}` map. **A user with no mapping is not a reader** — they get
an explanatory 403, not a picker.

**The authorization property is structural rather than checked**, which is the
part worth verifying yourself: the session-authenticated routes *take no stream
parameter at all*. The stream is looked up from the verified user id. There is
no request field a reader could tamper with to read someone else's paper,
because there is no such field. Grep the URL map: `<stream>` appears only in
the token-authed `PUT`.

### 3.3 Token verification

`SessionVerifier.verify` (`sessionauth.py:326`). The order is deliberate —
cheap structural refusals first, signature last, so a malformed token never
reaches the key cache and cannot be used to probe it.

1. Not three dot-separated segments → refuse (`:336`).
2. Header unreadable → refuse (`:340`).
3. **`alg` is not exactly `RS256` → refuse** (`:349`). The algorithm is a
   constant (`sessionauth.py:76`), never read from the token. This kills
   `alg:none` and the RSA-public-key-as-HMAC-secret confusion **before any key
   lookup**, and PyJWT's one-item algorithm list at `:365` kills them again.
   Two locks, because this is the door.
4. No `kid`, or `kid` not in the key set → refuse (`:351`, `:356`).
5. `PyJWT.decode` with a pinned algorithm list, expected issuer, expected
   audience, 30s leeway, and `require: [exp, iat, sub, iss, aud]` (`:364-373`).
6. Empty/whitespace `sub` → refuse (`:377`).

**We verified the algorithm pin guards a real hole, not a theoretical one.** A
deliberately naive verifier (algorithm taken from the header, key = the public
modulus) **accepts** the HS256-confusion forgery in its raw-modulus spelling.
The pin is load-bearing.

**Worth your scepticism:** `leeway=30` accepts a token expired up to 30 seconds
ago. Combined with §7.2's revocation window, that is 30 seconds on top of five
minutes. We think that is right for phones with imperfect clocks; you may
disagree.

### 3.4 The key cache

`JwksCache` (`sessionauth.py:213`). Three behaviours, each with a test:

* a key set is fetched at most once per hour (`:280`);
* **an unknown `kid` triggers exactly one refetch, then is rate-limited for 60
  seconds** (`:288`) — a rotation and a forgery look identical from here, so a
  stream of forged tokens carrying random `kid`s must not become a fetch storm
  against the vendor or a latency amplifier against us. Measured: 25 forgeries
  → 2 fetches;
* **a failed fetch never discards keys we already hold** (`:266`) — an
  unreachable vendor must not sign every reader out. This is the one
  deliberately swallowed exception in the file, and the comment says why.

There is also an operator-pinned mode (`NEWSLENS_STYTCH_JWKS`, a verbatim key
set in the environment) under which **the reading path opens no socket to
anyone**. Test: `test_an_operator_pinned_key_set_opens_no_socket_at_all`.

**Worth your scepticism:** the 60-second cooldown means a genuine emergency key
rotation takes up to a minute to be picked up. We judged that acceptable.

### 3.5 The cookie

Set in exactly one place, `open_session` (`app.py:652`), and it is the only
`set_cookie` in the service.

`HttpOnly` · `SameSite=Lax` · `Path=/` · `Max-Age=31622400` (366 days) ·
`Secure` **unless** the request is plaintext loopback (`_cookie_secure`,
`webhelpers.py:143`).

Two things to check hard:

* **`Secure` fails closed in the direction that matters** — anything not on
  loopback gets the flag, so a cookie can never go to a remote reader over
  plaintext. The loopback exemption exists because a `Secure` cookie is simply
  never stored on `http://127.0.0.1`, which would make the seam untestable. A
  **missing** `remote_addr` is treated as loopback (that is the in-process test
  client, which speaks no socket). Convince yourself that cannot be induced
  from outside.
* **The cookie lives 366 days; the JWT inside it lives five minutes.** That gap
  is the design, not an oversight — see §7.2.

**Why an exchange at all,** rather than reading the vendor SDK's own cookie:
the SDK's cookies are readable by any script on the page, by design. Ours is
readable by none. The exchange is what buys `HttpOnly`.

**CSRF on the exchange:** `POST /api/session` requires the custom header
`X-NewsLens-Session: 1` (`app.py:619`). A cross-site form or image cannot set a
custom header without a CORS preflight, and this service answers no preflight.
Without it a hostile page could log a reader into the *attacker's* account
(session fixation) or out of their own.

### 3.6 The redirect

`_safe_next` (`webhelpers.py:111`) gates the `?next=` return path. An open
redirect on a sign-in page is the classic phishing stepping stone.

It **checks rather than sanitises** — one leading slash, no `//`, no `/\`, no
scheme colon before `?`, no `\r \n \t \\ space`, ≤512 chars — and anything
failing goes to `/`. "Refused rather than repaired: a value we had to fix is a
value we did not understand."

**A comment-vs-code gap we found and then closed.** The docstring claimed "no
control characters" while the check named only the five bytes above — two of
which, backslash and space, are not control characters at all — so an embedded
NUL or other C0 byte was *kept*. No origin escape existed (`//`, scheme colon
and backslash are all blocked, and the value is `quote()`d into the redirect
and JSON-escaped into the page), which is why the first draft of this document
disclosed it to you rather than patching it. **Our own review then closed it**:
the whole C0 range plus DEL is now refused by codepoint, and the docstring is
true. Receipt: `/foo\x00bar` was measured *kept* at the pre-fix bytes and is
refused now (`test_a_control_character_in_the_return_path_is_refused`, which
fails against the previous version). We mention the history because you should
know which parts of this file were written before the fix and which after.

### 3.7 The content security policy

`_security_headers` (`app.py:443`) sets a CSP on every response.

The property that matters: **the identity provider's script origin appears on
`/login` and on no other route.** A vendor `<script>` that ever leaked into a
served edition would be refused by the reader's own browser, rather than by our
good intentions.

**The allowed origin is derived from the URL the page actually loads**
(`login_csp_for`, `app.py:133`, computed once per app from
`NEWSLENS_STYTCH_SDK_URL`), and the derived origin *replaces* the built-in one
rather than joining it. This is not decoration: §7.3's repair path is
"retarget that variable", and until our own review caught it the policy was
baked from a module constant at import — so following our own runbook made
`/login` refuse the very script it had just been pointed at, and the page then
blamed its own missing keys. A URL that is not an https origin (the one
exception being a plaintext loopback, for the local walk) is **refused and the
policy falls back to the pinned default**, with one line on stderr; a typo can
narrow this policy, never widen it.

Common to every route (`app.py:96`): `default-src 'self'`, `worker-src 'self'`,
`style-src 'self' 'unsafe-inline'`, `img-src 'self' data:`, `font-src 'self'
data:`, `form-action 'none'`, `frame-ancestors 'none'`, `base-uri 'none'`,
`object-src 'none'`.

`worker-src 'self'` is spelled out rather than left to the fallback chain
deliberately: a worker's script falls back through `child-src` to `script-src`,
and `script-src` on `/login` names the vendor. The offline path is too
important to rest on which fallback a given browser implements.

**`script-src` includes `'unsafe-inline'`, and you should push on this.** Our
reasoning: an edition is one self-contained document carrying its own inline
script and stylesheet — that is what "frozen document" means — and hashing them
would mean putting a second copy of every edition's bytes in the server. The
mitigating facts are that the inline content is generated by our own laptop
(not user input, not third-party), and that no third-party origin is allowed on
any reader route. We are not fully comfortable with it either; it is the single
biggest concession in the file.

---

## 4. The publish path — the only route that accepts data

`push_edition` (`app.py:661`). Order of checks:

1. **Bearer token → stream.** `_stream_for_token` (`webhelpers.py:166`) hashes
   the presented token and compares with `hmac.compare_digest` (constant time),
   **and compares against every configured stream even after a match**, so
   neither the token nor the number of streams leaks through timing. The host
   stores only sha256 digests — a stolen server discloses nothing that lets
   anyone publish.
2. **Stream and date must be legal names** (`valid_stream`, `valid_date` in
   `hoststore.py:88,104`) — the path traversal gate.
3. **The stream comes from the TOKEN, never the URL.** A mismatch is refused
   (403), not silently redirected, so a misconfigured laptop is loud on its
   first push instead of writing into another paper.
4. **Size cap**, checked twice — Flask's `MAX_CONTENT_LENGTH` and an explicit
   comparison (`app.py:676`).
5. **The body must be an edition** (`_bundle_problem`, `webhelpers.py:183`):
   valid JSON object, non-empty `html`, a 64-hex `content_sha256`, and — the
   important one — **the digest is recomputed server-side and compared with
   `compare_digest`**. A truncated upload and a corrupted one look identical to
   a length check; they do not look identical to a digest.
6. **The declared `edition_date` must match the URL date.**
7. **The html must be servable** — `</head>` and `</body>` must each occur
   exactly once, the same discipline `augment_edition` enforces below. This
   check is new, and it is here because our own review found the hole: a
   sha-valid, correctly-dated bundle with no `</head>` was **stored 200**, the
   `latest` pointer moved to it, and every subsequent read — the front page
   first — answered **500** until a good re-push. Reaching it needs the
   stream's own push token, so it is an operator-self-inflicted outage rather
   than an escalation, but it contradicted point 3's own principle: the
   misconfigured laptop should be loud on its **first push**.

Writes are atomic (temp file + rename, unique temp per writer — a shared temp
name was a real race we fixed and pinned). Re-pushing identical bytes is
idempotent, records a receipt, and **repairs the `latest` pointer** — that last
part matters because it is the documented disaster-recovery verb.

**`augment_edition` (`app.py:328`) is the one place the host touches edition
bytes.** It inserts two spans at `</head>` and `</body>`, each of which must
occur **exactly once** or it raises — an anchor appearing twice would silently
insert into the wrong place. Removing the inserted spans returns the pushed
document byte for byte, and a test asserts exactly that. The JSON island it
inserts is escaped with `.replace("<", "\\u003c")`; **that escaping is worth
your eye** — it is the one hand-rolled HTML/JS boundary in the service.

---

## 5. Faked seams — the complete list

Convention: anything demo-faked carries a `PROTOTYPE: faked` marker. Grep for
it. There are **three markers in the shipped service**, describing **two**
seams:

| Seam | Where | What it fakes | Reachable in production? |
|---|---|---|---|
| `NEWSLENS_DEV_NO_AUTH` | `app.py:60`, `app.py:394` | skips session verification, treats the single mapped user as signed in | **No** — refuses to boot off loopback, and 403s non-loopback callers even if it does (§6) |
| `window.__NEWSLENS_AUTH__` | `login.js:267` | replaces the whole vendor adapter, so the sign-in page can be driven with no vendor account | **No** — nothing in the shipped code sets it; it is checked first so a stub is unambiguous |

The fourth grep hit is in the test suite, and the fifth is in packaging
metadata. **Nothing else in the hosted service is faked.** Everything else —
the token compare, the JWT verification, the atomic writes, the ledger — is
real code doing the real thing.

---

## 6. The development bypass — the thing most likely to end badly

We consider this the highest-risk item in the packet, because it is the one
whose failure mode is "the paper is on the internet with the door open."

**Two independent guards.**

**At boot** — `dev_bypass_guard` (`app.py:220`) raises `SystemExit` at import,
which kills gunicorn before it accepts a connection. It refuses if the bypass
is on **and** either: any hosting-platform signal is in the environment
(`FLY_APP_NAME`, `KUBERNETES_SERVICE_HOST`, `DYNO`, … — `app.py:170`), or any
bind it can see is not loopback. It reads binds from four sources
(`declared_binds`, `app.py:191`): gunicorn's own argv, `GUNICORN_CMD_ARGS`,
several environment variables, and the parsed `--host`. **It fails closed on
ambiguity** — an unparseable bind string is treated as non-loopback.

**At request time** — `_bypass_is_loopback_only` (`app.py:466`),
registered *only* when the bypass is on. Any request whose peer is not loopback
gets a 403 and nothing else: no page, no edition, no push.

**The second guard exists because the first one cannot win.** Enumerating bind
channels is a race by construction: `GUNICORN_CMD_ARGS` was found in review
after the first version shipped, and a `gunicorn.conf.py` holding `bind=` is
the same class and is *not* parsed by anything above. So the refusal also
stands at the moment that always exists — the request itself.

**Named limitation, not solved:** a same-host reverse proxy deliberately
forwarding into a bypass instance presents loopback peers and would pass. That
is a two-step operator act rather than the tired evening this is written about.
With real auth on, the request guard is not even registered — the session check
is the access rule.

We drove nine boot cases as real processes at these bytes (six refusals, three
correct boots). **Please try to defeat both guards.** A bypass reachable from
outside is the finding that matters most in this packet.

---

## 7. Known limitations — accepted, on the record

### 7.1 The reading ledger sees less than you would assume

One row per authenticated edition `GET` (`record_read`, `hoststore.py:356`).
Three limitations, all deliberate, all recorded in-code:

1. **Offline reads are invisible.** A service-worker cache hit never reaches
   the server. A genuinely phone-only week under-counts itself, and we have no
   way to fix that without a client event API we deliberately did not build.
2. **There is no client event API** — no inbound write channel of any kind
   beyond the two routes in §3.1. Adding one is a design decision, not a patch.
3. **The laptop's own analytics don't see phone reads**, so a separate
   long-running measurement on the laptop under-counts once phone reading
   starts. Disclosed to its owner; not a security property.

Stored per read: stream, date, kind, opaque user id, timestamp. No IP, no user
agent, no content.

### 7.2 Revocation has a five-minute envelope, and the host cannot revoke

**This is the most important honest limitation in the packet.**

The identity provider issues session JWTs with a fixed five-minute lifetime.
We verify them locally against a cached public key. Therefore: **a session
revoked at the provider still opens this paper for up to five minutes** (plus
30s leeway), and **this host cannot revoke anything at all** — it holds no
vendor secret, so revocation is the provider's act, effected by refusing to
issue the next refresh.

We chose this consciously over the alternatives:

* *Host holds the vendor secret* → rejected. It would let a public, rented,
  internet-facing box mint and refresh sessions. That is precisely the power we
  did not want it to have, and it is why the host is structurally secretless.
* *Host mints its own sessions* → rejected as hand-rolled session management.

For a single-operator paper with a handful of readers, a five-minute window on
a revoked session is a cost we accept. **For a different deployment it might
not be**, and you should say so if you think we are wrong.

### 7.3 The identity provider's SDK — two live defects we found and did not fix

We fetched the exact SDK artifact our code loads and compared it against what
our sign-in page calls. **Five of seven assumptions were correct. Two are
wrong.** Full receipts in `DEPLOY.md` ("THE SDK ARTIFACT DOES NOT MATCH THE
ADAPTER").

* **`client.passwords.authenticate(…)` does not exist** in that artifact — the
  namespace is absent entirely. Since passkey *enrolment* is deferred until a
  domain is chosen, the password arm is the only route onto a new device, so
  first sign-in would raise a `TypeError`.
* **`client.session.getTokens()` does not exist either**, and this one fails
  **silently**: the call is guarded, so the silent-refresh path simply never
  fires, and every five-minute lapse becomes a manual sign-in rather than an
  invisible bounce.

We did not guess a repair. The artifact was last modified in 2022 and bundles
React 17 — consistent with that URL being a legacy loader while the current SDK
ships on npm — so coding against its shape could easily be the wrong fix. The
SDK URL is an environment variable precisely so this is a configuration change,
and as of this review that is now true end to end: the login page's own CSP
follows that variable (§3.7), which it did not before.

**We took our own recommendation, in a different shape than we first wrote it.**
The original note here said a missing method should render the existing "can't
be reached" state. That would have been another wrong sentence: the script
*was* reached, it is simply the wrong shape, and "can't be reached" would send
whoever reads it to check the network. So `login.js` now takes an inventory of
the four methods it needs, over the real client and never over the test stub,
and on any sign-in attempt a non-empty inventory renders one plain-ink line —
"This page loaded an unexpected version of its sign-in service." — with a
second line saying sign-in may not work or may not last. A `TypeError` from an
arm whose method is in that inventory is attributed to the version, never to
the network. **The floor never blocks an arm**: a false positive can add a
line and nothing else, and an arm whose methods are all present still signs a
reader in.

This does not fix the mismatch and is not meant to; it makes the mismatch — and
any future one, since that URL is unversioned and the vendor can change the
artifact's shape any day — impossible to suffer silently. **If you review one
thing in `login.js`, review the adapter at `login.js:273-305` and the floor at
`login.js:75-118`**, and tell us whether the right move is retargeting the URL or
rewriting the adapter.

### 7.4 Other things you should know

* **Sign-in requires the network; reading does not.** The cache is never
  auth-gated — a 401, a 403 and a redirect are all "the network can't give you
  the paper right now" to the service worker, and the cached edition answers
  all three. A dead session does not take yesterday's paper off someone's
  phone on a train.
* **The archive starts empty and is never back-filled.** Editions published
  before this system existed have no frozen document and will not get a
  reconstructed one — reconstructing would render *today's* state and stamp it
  as frozen at publish, which would be a lie.
* **Losing the volume is recoverable** — every edition re-pushes from the
  laptop.
* **This host is not designed for many users.** One SQLite ledger, files on one
  volume, no horizontal scaling story. It is a paper for its author and a few
  invited readers.

---

## 8. Where we think we are weakest — read this before you plan your time

Ranked by our own honest worry, not by severity theatre.

1. **The development bypass** (§6). Highest blast radius. Two guards, and we
   still list it first.
2. **The SDK mismatch** (§7.3). Two of seven adapter assumptions are wrong
   against the artifact the code actually loads. The *mismatch* is unresolved
   and we are handing it to you unresolved; what we did fix is the silence —
   neither failure can now happen without the page saying so, and the
   attribution is the version rather than the network. Judge both the shape of
   that floor and the decision not to guess a repair.
3. **`'unsafe-inline'` in `script-src`** (§3.7). A real concession with a real
   reason. Tell us if the reason is not good enough.
4. **The JSON-island escaping in `augment_edition`** (§4) — the service's only
   hand-rolled HTML/JS boundary, and that class of code is where human review
   has historically paid most in this repo.
5. **The publish path's new anchor gate** (§4, check 7) — our own review found
   that the endpoint accepted a bundle the reader could not be served, and the
   fix was written during this review. It is the newest code in the packet, and
   the least sat-with; treat it as such.
6. **Claim shapes are unverified against a live token.** The expected `iss` and
   `aud` come from documentation, not from a token we have held; both are
   environment-overridable and `DEPLOY.md` carries the repair path. Nothing
   security-critical rests on them being *right*, only on them being *checked*
   — but confirm that reading yourself.
7. **We have never run this against the real identity provider.** No account,
   no keys, by construction. Every auth test uses our own generated keypair.
   The verification logic is exercised hard; the *integration* is not exercised
   at all.

---

## 9. Roads not taken — three decisions we want you to second-guess

Each of these was a point where the obvious implementation was rejected. If we
were wrong, this is where the error lives.

**1. The five-minute JWT vs. the "sign in once per device" requirement.**
The provider's session JWTs expire every five minutes; the product requires
credentials once per device and then not again for months. The three ways to
close that gap: hold the vendor secret and refresh server-side (rejected — puts
minting power on a public box); mint our own longer-lived session (rejected as
hand-rolled session management); or **let the browser refresh with the public
token while the host only ever verifies** (chosen). The 366-day `HttpOnly`
cookie carries the *vendor's* JWT, re-verified statelessly on every request. No
session store, no self-signed token, nothing minted here. **The cost is §7.2's
revocation envelope**, and that cost is real.

**2. Vendoring the provider's script.** We wanted to pin the third-party script
by hash, serve it ourselves, and tighten `/login`'s CSP to `'self'`. We could
not, and the evidence is in `DEPLOY.md`: there is exactly one artifact and it
is unversioned; **every versioned URL shape returns HTTP 200 with a 715-byte
HTML fallback**, including a randomly generated nonsense path — so a vendoring
script that checked for a 200 would have pinned an error page and reported
success. Worse, vendoring would not even buy a third-party-free page: the
bundle injects Google reCAPTCHA at runtime when a project setting we cannot see
enables it. So we pinned the **origin** instead and wrote down why. If you know
a way to pin this properly, we would like to hear it.

**3. Passkey enrolment.** Deferred, not forgotten. A passkey is bound to the
domain it is enrolled on, and the domain is a deploy-time choice, so enrolling
before that is throwing the enrolment away. There is also a provider
precondition we could not design around: **a verified email or phone is
required before a passkey can be registered at all**, and the password-creation
endpoint verifies neither. Which factor supplies that verification is an open
product decision, deliberately asked rather than guessed.

---

## 10. What pins what — the tests

Two files, **123 collected tests**, no network, no keys (104 `def test_` functions; the rest are parametrised cases). Three of them execute the shipped `login.js` under `node` against a stub DOM and skip if `node` is absent. Run: `.venv/bin/python -m pytest -q
tests/test_nl163_auth.py tests/test_nl163_hosted.py`.

| Claim in this document | Test |
|---|---|
| §3.3 every forgery class is refused | `test_a_foreign_key_claiming_a_kid_we_trust_is_refused`, `test_alg_none_is_refused`, `test_hs256_confusion_with_the_public_key_as_the_secret_is_refused`, `test_a_tampered_payload_is_refused`, `test_an_expired_token_is_refused_and_a_slightly_skewed_one_is_not`, `test_another_projects_token_is_refused`, `test_a_token_for_another_audience_is_refused`, `test_a_token_with_no_subject_is_refused` |
| §3.3 the algorithm is a constant, not a token field | `test_the_algorithm_is_pinned_in_the_source_not_taken_from_the_token` |
| §3.4 cache: fetch-once, one refetch then cooldown, outage ≠ sign-out, pinned = no socket | `test_the_key_set_is_fetched_once_and_then_reused`, `test_an_unknown_kid_refetches_once_and_then_stops`, `test_an_unreachable_vendor_does_not_sign_anyone_out`, `test_an_operator_pinned_key_set_opens_no_socket_at_all` |
| §3.5 cookie flags; `Secure` off-loopback only | `test_signing_in_sets_one_httponly_cookie_that_outlives_the_jwt`, `test_the_cookie_is_secure_off_the_loopback_and_only_off_the_loopback` |
| §3.5 a forged token never becomes a cookie; CSRF gate | `test_a_forged_token_never_becomes_a_cookie`, `test_the_exchange_refuses_a_cross_site_caller` |
| §3.2 an unmapped account gets an honest 403 | `test_an_account_with_no_paper_still_signs_in_and_is_told_so` |
| §3.6 the return path can only point at this paper | `test_the_return_path_can_only_point_at_this_paper` |
| §3.6 control characters are refused, real paths kept | `test_a_control_character_in_the_return_path_is_refused` |
| §3.7 the vendor origin is on `/login` and nowhere else | `test_only_the_login_page_may_reach_the_vendor`, `test_the_vendor_appears_in_exactly_one_template` |
| §3.7 the policy follows the configured SDK URL, replaces rather than joins, and every other route stays clean under a retarget | `test_the_login_policy_follows_the_configured_sdk_origin` |
| §3.7 a URL that is not an https origin falls back to the pin and says so | `test_an_sdk_url_that_is_not_an_https_origin_fails_closed_to_the_pin` |
| §7.3 a wrong-shaped SDK is named, not blamed on the network, and never blocks a working arm | `test_a_wrong_shape_sign_in_service_is_named_instead_of_blamed_on_the_network` |
| §7.3 a healthy client says nothing, on first paint or after a whole ceremony | `test_a_right_shape_client_says_nothing_and_the_ceremony_proceeds` |
| §7.3 a session that cannot silently refresh is named at the ceremony it forces | `test_a_session_that_cannot_be_refreshed_is_named_at_the_next_ceremony` |
| §3.1 exactly two non-GET routes; no secret in the deployable | `test_there_is_no_signup_surface_anywhere_in_the_service` |
| §6 the bypass refuses every non-loopback caller | `test_the_bypass_still_refuses_every_caller_that_is_not_this_machine` |
| §6 with real auth on, the bypass guard is not even registered | `test_the_real_lock_does_not_register_the_bypass_backstop` |
| §7.4 the door and the cache open with no session | `test_the_door_and_the_cache_open_with_no_session_at_all`, `test_the_service_worker_still_reads_a_login_redirect_as_a_refusal` |
| §4 idempotent push, sha mismatch, cross-stream, traversal, oversize | `tests/test_nl163_hosted.py` (push section) |
| §4 check 7: an unservable bundle is refused at the push, and a good one still serves after it | `test_a_bundle_the_serve_path_cannot_serve_is_refused_at_the_push`, `test_a_good_bundle_still_lands_and_still_serves_after_the_push` |
| §4 stripping the augmentation returns the pushed bytes exactly | `tests/test_nl163_hosted.py` (augmentation section) |
| ledger survives 12 concurrent first-contact writers, 120 rounds | `test_twelve_writers_meeting_a_brand_new_ledger_all_land_every_round` |

**On the quality of these tests, honestly:** they were written adversarially
and several were verified to fail when the protection is removed — including
the concurrency pin above, which we measured catching a reverted fix in 20 runs
out of 20 (its predecessor caught it in 5 of 20, which is why it was rewritten).
But they were written by the same kind of system that wrote the code. **A test
passing here means we thought of the case.** The cases we did not think of are
what you are for.

---

## 11. Reporting back

The most useful outputs, in order: (1) a way to reach an authenticated route
without a session; (2) a way to make the development bypass serve a non-local
caller; (3) a way to publish to a stream you do not hold the token for; (4) a
judgement on §3.7's `'unsafe-inline'` and §7.2's revocation envelope — those
are accepted risks, and an outside opinion on whether they should stay accepted
is worth as much to us as a bug.
