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
