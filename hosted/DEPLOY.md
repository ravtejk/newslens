# Deploying the paper's host

**Status: this ships dark.** The org built and verified everything below on
loopback and deployed nothing. Public exposure is gated on the NL-33 human
engineering review; the account, the domain, the volume and every secret are
the principal's hands (the org never creates accounts, never holds keys, never
deploys).

**Auth is not wired yet.** Milestone 2 is the reader and the receiver;
milestone 3 is the lock. Until then the only way in is the loopback-only
development bypass described at the bottom — which refuses to boot anywhere a
stranger could reach it.

---

## What this service is

A door and a shelf. The Mac generates an edition, freezes it into one
self-contained document, and PUTs it here. A reader signs in and is served that
document. The host never renders an edition and holds no product data — no
stories, no threads, no follow state. It stores bundles as files and keeps one
SQLite ledger of when they arrived and when they were read.

```
/data/streams/<stream>/<date>.json     the pushed bundle, verbatim
/data/streams/<stream>/latest          pointer file (a date)
/data/ledger.db                        push_receipts + read_events
```

Losing the volume is a non-event: every edition is re-pushable from the Mac's
own artifacts (`newslens push --date <d>`).

## Environment

| Variable | Required | What it is |
|---|---|---|
| `NEWSLENS_HOSTED_DATA` | yes (image sets `/data`) | the volume root |
| `NEWSLENS_PUSH_TOKENS` | yes | `{"<stream>": "<sha256 of the push token>"}` — **digests only**; the token itself lives in the Mac's `.env` and nowhere else |
| `NEWSLENS_USER_STREAMS` | yes | `{"<user id>": "<stream>"}` — one account reads one paper; there is no picker |
| `NEWSLENS_DAY_OFFSET_MINUTES` | no (0) | the paper's day boundary from UTC; decides only whether the newest edition counts as "today's" |
| `NEWSLENS_MAX_BUNDLE_BYTES` | no (10485760) | body cap on a push; a real edition is ~150KB |
| `NEWSLENS_DEV_NO_AUTH` | no | the loopback-only bypass — see below |
| `NEWSLENS_DEV_USER` | no | dev-seam only: which mapped user the bypass treats as signed in (needed when `NEWSLENS_USER_STREAMS` holds more than one). **Inert unless `NEWSLENS_DEV_NO_AUTH` is on** |
| `NEWSLENS_STYTCH_PROJECT_ID` | yes (M3) | the auth project this paper's sessions belong to. Public |
| `NEWSLENS_STYTCH_PUBLIC_TOKEN` | yes (M3) | the **publishable** token the sign-in ceremony uses. Public by design — it is in the page's own HTML |
| `NEWSLENS_STYTCH_JWKS_URL` | no (derived) | where the verifier reads the vendor's public keys; defaults to the documented URL for this project |
| `NEWSLENS_STYTCH_JWKS` | no | the key set pasted verbatim (`newslens phone-account jwks` on the Mac prints it). Set it and **the reading path opens no socket to anyone**; costs a re-paste at each ~6-month rotation |
| `NEWSLENS_STYTCH_ISSUER` | no (derived) | expected `iss`; defaults to `stytch.com/<project id>` |
| `NEWSLENS_STYTCH_AUDIENCE` | no (derived) | expected `aud`; defaults to the project id |

**NO VENDOR SECRET IS EVER HELD HERE, and that is structural rather than
careful.** Every name above is public. This service cannot create an account,
cannot mint a session and cannot revoke one: it checks a signature against a
public key, and that is the whole of its authority. The operator's secret
(`STYTCH_SECRET`) lives on the Mac and is used by one command,
`newslens phone-account`. If a variable holding a vendor secret ever appears in
this table, that is the finding.

The two *derived* claim names exist because the claim shapes are the part most
likely to be wrong: they are documented as `stytch.com/<project id>` and the
project id, sourced secondarily, and **not verified against a live token** —
the org held no keys when this was built. If sign-in refuses everyone and the
logs say `InvalidIssuerError` or `InvalidAudienceError`, read the two claims
off a real token and set these two variables. That is the intended repair path,
which is exactly why they are variables and not constants.

Minting a push token (his hands, on the Mac):

```sh
python3 -c "import secrets;print(secrets.token_urlsafe(32))"     # the token
python3 -c "import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" <token>
```

The token goes in the Mac's `.env` as `NEWSLENS_PUSH_TOKEN`; the **digest**
goes in `NEWSLENS_PUSH_TOKENS` here. The host can verify a token and can never
reveal one. Rotation is the same two commands plus a `fly secrets set`.

## Deploy (his hands)

```sh
cd <repo root>
fly launch --no-deploy --copy-config --config hosted/fly.toml
fly volumes create newslens_data --size 1 --region <region>
fly secrets set NEWSLENS_PUSH_TOKENS='{"main":"<digest>"}' \
                NEWSLENS_USER_STREAMS='{"<user id>":"main"}'
fly deploy --config hosted/fly.toml --dockerfile hosted/Dockerfile
```

Then on the Mac, in `.env`:

```
NEWSLENS_PUSH_URL=https://<app>.fly.dev/api/streams/main
NEWSLENS_PUSH_TOKEN=<the token>
```

`newslens doctor` reports both, and `newslens push` sends the newest frozen
edition. The next generate pushes on its own.

**Domain before passkeys.** Passkeys are bound to the domain they are enrolled
on (subdomains of one registration are fine). Choose the final hostname before
anyone enrolls a passkey in M3, or the enrollment is thrown away.

## Running it locally

```sh
NEWSLENS_HOSTED_DATA=./data/hosted \      # under data/, which is gitignored
NEWSLENS_DEV_NO_AUTH=1 \
NEWSLENS_USER_STREAMS='{"local":"main"}' \
NEWSLENS_PUSH_TOKENS='{"main":"<digest>"}' \
python3 -m hosted.app --host 127.0.0.1 --port 5473
```

Service workers need a secure context; `http://localhost` counts as one, so the
offline behaviour is testable without TLS.

## The one faked seam

`NEWSLENS_DEV_NO_AUTH=1` skips session verification and treats the single
mapped user as signed in. It exists so the milestone's own verification loop
can drive the five states and the service worker without a vendor account.

**It refuses to boot on anything but loopback.** The check runs at import — so
it kills gunicorn before it accepts a connection — and it fails closed:

* any `--bind`/`-b` (gunicorn's own argv **or `GUNICORN_CMD_ARGS`**),
  `NEWSLENS_BIND`, `HOST` or `--host` that is not `127.0.0.0/8` or `::1` →
  refuse;
* any hosting-platform signal in the environment (`FLY_APP_NAME`,
  `KUBERNETES_SERVICE_HOST`, `DYNO`, …) → refuse, whatever the bind says;
* an address it cannot parse → refuse.

**And a backstop at request time**, because enumerating bind channels loses
that race by construction (a `-c gunicorn.conf.py` holding `bind=` is not
parsed by anything above): while the bypass is on, any request whose peer
address is not loopback is answered `403` and served nothing — no page, no
edition, no push. A same-host reverse proxy deliberately forwarding into a
bypass instance presents loopback peers and would pass; that is a two-step
operator act, and with real auth on (M3) the backstop is not even registered —
the session check is the access rule.

Deployment therefore cannot carry it. `hosted/app.py::dev_bypass_guard` and
`create_app`'s `_bypass_is_loopback_only`.

## The lock (M3)

One cookie, verified locally. `hosted/sessionauth.py` reads the vendor's public
keys once, caches them, and checks every session JWT for a pinned RS256
signature, the configured issuer and audience, and an unexpired `exp` with 30
seconds of clock-skew leeway. There is no vendor round trip on the reading
path, so a vendor outage costs new sign-ins and not the morning.

**The session cookie** (`nl_session`) is HttpOnly, SameSite=Lax, `Secure`
everywhere except a plaintext loopback request, and lives 366 days. The JWT
inside it lives five minutes — the vendor's fixed lifetime. The gap is
deliberate and is what "credentials once per device" is made of: when the proof
lapses, the reader is bounced to `/login`, whose script silently exchanges the
long-lived vendor session for a fresh proof and returns them to the page they
asked for. The redirect carries `?next=`, checked against an open-redirect
allowlist (one leading slash, no scheme, no authority).

**The exchange** is `POST /api/session` — the only non-GET route besides the
push endpoint. It requires a same-origin custom header, so a hostile page
cannot plant a session in a reader's browser, and it writes exactly one cookie:
no row, no file, no state. Read-pure holds.

**The vendor's script loads on `/login` and nowhere else**, and the pin is a
`Content-Security-Policy` rather than a promise: the login route's policy names
the vendor origin, every other route's policy admits no third-party script at
all. A vendor tag that ever appeared in a served edition would be refused by
the reader's browser.

**The cache is never auth-gated.** A 401, a 403 and a `/login` redirect are all
"the network cannot give you the paper right now" as far as the service worker
is concerned, and the cached edition answers all three. A dead session does not
take yesterday's paper away from somebody on a train.

## The vendor's script: why it is not vendored (M4 — settled, with receipts)

M3 pinned the vendor's script by ORIGIN because the loader URL carries no
version and so cannot carry an SRI hash. M4 was chartered to try harder: fetch
a pinned, versioned build, vendor it into `hosted/static/`, add Subresource
Integrity, and tighten the login page's CSP to `'self'`.

**It cannot be done, and here is the evidence rather than the conclusion.**
Everything below is a read-only GET to their CDN (`js.stytch.com`) on
2026-08-31. No credential was sent, no API host was contacted, $0.

1. **There is exactly one artifact, and it is unversioned.**
   `https://js.stytch.com/stytch.js` → 200, 798,383 bytes, `sha256
   215ad0b60644282c503c3ff279ccbf2b385f1ddfb2943f84e58b6dd68af72f99`,
   `etag "f40720271bd9090dc2e6f5ffe890614a"`, `last-modified 2022-10-03`.
   Two fetches two seconds apart returned identical bytes.
2. **Every versioned URL shape is a decoy — and this is the part worth
   knowing.** `/stytch.v1.js`, `/v1/stytch.js`, `/stytch-17.0.2.js`,
   `/versions.json`, `/package.json`, `/stytch.js.map`, `/` and a randomly
   generated nonsense path **all return HTTP 200**, each serving the same
   715-byte SPA HTML fallback rather than JavaScript. A vendoring script that
   fetched `stytch-17.0.2.js`, checked for a 200 and computed an SRI hash
   would have pinned an HTML error page and reported success. The 200 is not
   a file; it is a catch-all.
3. **Vendoring would not even buy a third-party-free page.** The bundle
   injects `https://www.google.com/recaptcha/enterprise.js` at runtime when a
   project has captcha enabled — a SECOND third party, chosen by a dashboard
   setting invisible from here — and it hard-codes `https://js.stytch.com` as
   its own frame origin. A vendored copy still reaches the vendor, so
   `script-src` could not tighten to `'self'` regardless.
4. **Not licence-clean to redistribute.** No LICENSE is served beside the
   artifact and no package metadata is obtainable from the CDN; the six
   `MIT license` strings inside it belong to bundled dependencies, not to the
   vendor's own code.
5. **A frozen private copy of an auth SDK is a worse posture, not a better
   one** — it means never receiving the vendor's security fixes for the one
   component whose entire job is credentials.

**Outcome: the M3 origin pin stands as ruled, and it is now the considered
answer rather than merely the available one.** Revisit only if the vendor
publishes a versioned, SRI-able artifact.

## ⚠ THE SDK ARTIFACT DOES NOT MATCH THE ADAPTER (M4 — read before deploying)

Having the bundle in hand settled M3's "unverifiable at $0" list against a real
artifact instead of against documentation. **Five of seven assumptions are
confirmed correct; two are wrong.** Receipts are greps against the sha above;
the adapter is `hosted/static/login.js:273-305`.

| The adapter assumes | The artifact `NEWSLENS_STYTCH_SDK_URL` serves | |
|---|---|---|
| `window.Stytch` is a callable factory | UMD ends `return function(e,t){return new mD(e,t)}` | ✅ |
| `factory(public_token)` positional | first constructor arg is the token | ✅ |
| `client.webauthn.authenticate({session_duration_minutes})` | `this.webauthn=new oI(…)` present | ✅ |
| `client.session.authenticate({session_duration_minutes})` | `class jB { authenticate(e) }` present | ✅ |
| response carries top-level `session_jwt` | `{…, session_jwt: t.session_jwt}` | ✅ |
| **`session_duration_minutes` is snake_case** | 32 snake_case hits at real call sites; camelCase 2 | ✅ **the item QA named the silent killer is CORRECT** |
| **`client.passwords.authenticate(…)`** | **no `passwords` namespace exists.** The client constructs `user, magicLinks, oauth, session, otps, cryptoWallets, webauthn, totps`; the string `passwords` appears **0 times** in 798KB | ❌ **throws** |
| **`client.session.getTokens()`** | the session class has exactly `getSync()`, `authenticate()`, `revoke()` | ❌ **silently undefined** |

**What each failure costs, plainly:**

* **`passwords` absent is fatal to first sign-in.** Passkey *enrolment* is
  deferred (it needs your domain), so the password arm is the only way onto a
  NEW device. Against this artifact it raises a `TypeError`.
* **`getTokens` absent is SILENT and breaks the §9 law quietly.** The call is
  guarded (`client.session.getTokens && …`), so nothing throws — `canRefresh()`
  returns `false` forever, the silent-return refresh never fires, and every
  five-minute lapse becomes a manual sign-in instead of an invisible bounce.
  "Credentials once per device" would fail with no error anywhere.

**This was NOT repaired by guessing, deliberately.** The artifact's
`last-modified` is 2022 and it bundles React 17, consistent with
`js.stytch.com/stytch.js` being a LEGACY loader while the current SDK ships on
npm as `@stytch/vanilla-js`. Rewriting the adapter against a 2022 bundle could
easily be the wrong repair, and the enrolment-factor question standing behind
the password arm is a decision the org routed to you, not one to settle in
code.

**The repair path is one variable, which is why it is a variable.** Point
`NEWSLENS_STYTCH_SDK_URL` at the SDK build your project's dashboard actually
tells you to load, then re-check the two ❌ rows against it. If the modern SDK
keeps `passwords` and renames `getTokens`, the adapter is a two-line change in
one file. **The login page's Content-Security-Policy follows that variable**
(`login_csp_for`, `app.py:133`): the derived origin *replaces* the built-in
`js.stytch.com` allowance, so the retarget really is one variable and not two.
Before this was true, retargeting made `/login`'s own policy block the script
you had just chosen, and the page then reported missing keys — a second wrong
diagnosis, arriving exactly while you repaired the first. A URL that is not an
https origin (a plaintext loopback excepted, for the local walk) is refused:
the policy falls back to the pinned default and says so once on stderr.

**Recommended and TAKEN, in a corrected shape.** The earlier recommendation
here was to render the existing §8 "can't be reached" state on a missing
method. That sentence would itself have been false — the script *was* reached,
it is simply the wrong shape — so `login.js` now takes an inventory of the four
methods it needs (over the real client, never over the test stub) and, on any
sign-in attempt with a non-empty inventory, renders **"This page loaded an
unexpected version of its sign-in service." / "Signing in may not work or may
not last; the paper's operator has the fix in the runbook."** A `TypeError`
from an arm whose method is in that inventory is attributed to the version,
never to the network. The floor never blocks an arm — one whose methods are all
present still signs a reader in — so a false positive can only add a line.
First paint stays silent. This does not repair the mismatch; it makes this
class of fault, including any future one behind that unversioned URL,
impossible to suffer silently.

## What is deliberately not here

* **No client event API.** The read ledger observes authenticated GETs on this
  side only. There is no inbound write channel, and adding one is a design
  decision, not a patch.
* **No follow verbs.** Stage A is read-pure: follow state renders as furniture
  because it was frozen at publish, and a verb here would mutate a database on
  a different machine.
* **No back-edition seeding.** An edition published before the bundle existed
  has no frozen document and will not get a reconstructed one — a rebuild would
  render today's follow state and stamp it frozen-at-publish. Day one is
  honestly empty.
* **No secrets in this repo.** The remote is public.
