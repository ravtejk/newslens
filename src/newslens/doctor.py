"""NewsLens doctor — plain pass/fail health report with a fix hint per line.

Run it as `scripts/doctor` (works even before pip install) or `newslens doctor`.

Design constraints (adr/0002-doctor-stdlib-first.md, incl. fix-loop-1 amendment):
  * Must run cleanly on a machine with NO keys granted and NO pip install yet:
    stdlib-only at import time (config qualifies — it imports yaml lazily);
    third-party imports guarded inside checks; missing OR unreadable anything
    is a friendly report line, never a traceback.
  * Read-only toward real state: the doctor never creates or alters the real
    database (db's query API is read-only by construction). Sole deliberate
    exception: the data-directory writability probe, which is the check.
  * Validation logic lives in config (single source of truth) — this module
    renders results, it must not re-implement rules (BUG-1 postmortem).
  * Every external call has a timeout and a visible failure path
    (team/ENGINEERING.md). With no keys present, no external API is called —
    except resolving any RSS feeds the principal has actively configured.
  * Prints an estimated cost-per-run (ENGINEERING.md doctor requirement) —
    static from the spec until the pipeline exists to measure real runs.

Exit code contract (QA relies on this):
  * 0 — everything required for a real daily run is in place (warnings allowed)
  * 1 — at least one required item is missing or failing (any ✗ line)

Marker legend: ✓ pass · ✗ required, failing · ⚠ action needed / worth a look ·
○ informational.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config, llm, paths  # config/llm stdlib-only at import time

PASS = "✓"
FAIL = "✗"
WARN = "⚠"
INFO = "○"

OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
PERPLEXITY_CHAT_URL = "https://api.perplexity.ai/chat/completions"
OPENAI_TIMEOUT_S = 15
ANTHROPIC_TIMEOUT_S = 15
PERPLEXITY_TIMEOUT_S = 20
# B3: the `claude` CLI version floor for headless effort control (ADR-0014
# spike #5 — /effort rides v2.1.205+). Below it, the subscription lane still
# runs (rank/editor/script send no effort), but effort-bearing seats can't map.
CLAUDE_VERSION_FLOOR = (2, 1, 205)
CLAUDE_VERSION_TIMEOUT_S = 10
FEED_TIMEOUT_S = 15  # WaPo's feeds measured 8-10s in the M2 sweep — headroom
USER_AGENT = "NewsLens-doctor/0.1 (personal prototype; one-user health check)"

INSTALL_HINT = (
    'python3 -m venv .venv && source .venv/bin/activate '
    '&& pip install --upgrade pip && pip install -e ".[dev]"'
)


class Result:
    def __init__(self, status: str, text: str) -> None:
        self.status = status
        self.text = text


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(paths.PROJECT_ROOT))
    except ValueError:
        return str(p)


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

def check_python() -> List[Result]:
    v = sys.version_info
    label = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 9):
        return [Result(PASS, f"Python {label} (>= 3.9 required) — {sys.executable}")]
    return [
        Result(
            FAIL,
            f"Python {label} is too old — NewsLens needs >= 3.9. "
            "On macOS: `xcode-select --install` provides 3.9, or `brew install python`",
        )
    ]


def check_deps() -> List[Result]:
    missing = []
    for module, pip_name in (("yaml", "PyYAML"), ("dotenv", "python-dotenv")):
        try:
            __import__(module)
        except ImportError:
            missing.append(pip_name)
    if not missing:
        return [Result(PASS, "Python deps importable (PyYAML, python-dotenv)")]
    return [
        Result(
            FAIL,
            "missing Python deps: " + ", ".join(missing) + f" — fix: {INSTALL_HINT}",
        )
    ]


def check_checkout() -> List[Result]:
    if paths.looks_like_checkout():
        return [Result(PASS, f"project root looks right — {paths.PROJECT_ROOT}")]
    return [
        Result(
            FAIL,
            f"project root {paths.PROJECT_ROOT} doesn't look like the prototype "
            "checkout (pyproject.toml / migrations/ not found) — run scripts/doctor "
            "from the checkout; a non-editable install is unsupported (see README)",
        )
    ]


# ---------------------------------------------------------------------------
# Config & keys
# ---------------------------------------------------------------------------

def _parse_env_fallback(path: Path) -> Dict[str, str]:
    """Minimal .env reader used only when python-dotenv isn't installed yet,
    so the doctor can still diagnose keys pre-install. Deliberately simple:
    KEY=VALUE lines, `export ` prefix tolerated, full-line comments and blanks
    skipped, single/double quotes stripped. Install deps for full parsing."""
    values: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value[:1] in ("'", '"') and value[:1] == value[-1:] and len(value) >= 2:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_effective_env() -> Tuple[Dict[str, str], List[Result]]:
    """Values from .env, with the real process environment winning.
    Never mutates os.environ; never prints a secret value anywhere."""
    notes: List[Result] = []
    file_vals: Dict[str, str] = {}
    if paths.ENV_FILE.exists():
        try:
            try:
                from dotenv import dotenv_values

                file_vals = {k: v for k, v in dotenv_values(paths.ENV_FILE).items() if v}
                notes.append(Result(PASS, f".env found ({_rel(paths.ENV_FILE)})"))
            except ImportError:
                file_vals = _parse_env_fallback(paths.ENV_FILE)
                notes.append(
                    Result(
                        PASS,
                        ".env found — parsed with the built-in fallback reader "
                        "(python-dotenv not installed yet)",
                    )
                )
        except OSError as exc:
            # e.g. PermissionError: keys can't load from the file, but the
            # doctor must report that friendly, not crash (BUG-2 class).
            file_vals = {}
            notes.append(
                Result(
                    FAIL,
                    f".env exists but is not readable ({exc}) — fix its "
                    "permissions (chmod 600 .env); until then keys in it "
                    "cannot load and will report as not set below",
                )
            )
    else:
        notes.append(
            Result(
                INFO,
                ".env not found — run: cp .env.example .env  "
                "(then fill keys in; .env is gitignored, never commit it)",
            )
        )
    env = dict(file_vals)
    env.update(os.environ)  # real environment wins
    return env, notes


def check_openai_key(env: Dict[str, str]) -> List[Result]:
    """A″ (2026-07-17): required precisely when — under the current seat map + lane
    env — some seat resolves to the OpenAI provider (gpt-4o). Post-B4 that is only
    the state/memory seat (and synthesis, which has no live call site yet);
    rank/editor/script/analyst/writer/follow_altitude are anthropic and the OpenAI
    key is INERT for them. So a keyless install with all-anthropic content seats is
    HEALTHY — reported plainly, not as a failure (the prior blanket FAIL was stale
    once the content seats flipped off gpt-4o). Gate ruling 2 (2026-07-17): a
    DORMANT seat (declared, no live call site — llm.DORMANT_SEATS) never forces
    the key requirement; there is no run for the key to protect. B6 re-arms it by
    removing synthesis from that set. The value is never echoed."""
    openai_seats = sorted(
        name for name in llm.SEATS
        if name not in llm.DORMANT_SEATS
        and llm.resolve_seat(name, env).provider == "openai"
    )
    dormant_openai = sorted(
        name for name in llm.DORMANT_SEATS
        if llm.resolve_seat(name, env).provider == "openai"
    )
    key = (env.get("OPENAI_API_KEY") or "").strip()
    if not openai_seats:
        # No LIVE seat routes to OpenAI — the key is not needed;
        # keyless-with-all-anthropic is healthy (dormant declarations don't
        # force a purchase; gate ruling 2, 2026-07-17).
        dormant_txt = (
            f" ({', '.join(dormant_openai)} declares gpt-4o but has no live "
            "call site until B6)" if dormant_openai else ""
        )
        if key:
            return [Result(INFO, "OPENAI_API_KEY set but no live seat currently "
                                 f"routes to OpenAI (gpt-4o) — unused{dormant_txt}")]
        return [Result(INFO, "OPENAI_API_KEY not needed — no live seat routes to "
                             f"OpenAI under the current seat map{dormant_txt}; "
                             "the anthropic content seats run keyless-OpenAI")]
    seats_txt = ", ".join(openai_seats)
    if not key:
        return [Result(
            FAIL,
            "OPENAI_API_KEY not set — get one at platform.openai.com/api-keys, "
            f"then add to .env. Required because the {seats_txt} seat(s) run "
            "gpt-4o (every anthropic content seat runs keyless-OpenAI — the key "
            "is needed ONLY for the openai seat(s)).",
        )]
    req = urllib.request.Request(
        OPENAI_MODELS_URL,
        headers={"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=OPENAI_TIMEOUT_S) as resp:
            payload = json.load(resp)
        elapsed = time.monotonic() - started
        count = len(payload.get("data", []))
        return [
            Result(
                PASS,
                f"OPENAI_API_KEY valid — read-only GET /v1/models OK "
                f"({count} models visible, {elapsed:.1f}s); powers the {seats_txt} "
                "seat(s)",
            )
        ]
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return [
                Result(
                    FAIL,
                    "OPENAI_API_KEY rejected (401) — mistyped or revoked; regenerate "
                    "at platform.openai.com/api-keys and update .env",
                )
            ]
        return [
            Result(
                FAIL,
                f"OpenAI check failed (HTTP {exc.code}) — key is present; retry in a "
                "minute or check status.openai.com",
            )
        ]
    except Exception as exc:  # URLError / timeout / DNS — network-shaped failures
        return [
            Result(
                FAIL,
                f"could not reach api.openai.com ({type(exc).__name__}) — key is "
                "present but unverified; check network/VPN/proxy and re-run",
            )
        ]


def check_anthropic_key(env: Dict[str, str]) -> List[Result]:
    """The Claude API lane credential (B2). Required precisely when — under the
    current seat map + lane env — some seat resolves to the anthropic provider
    (rank/editor/script run Haiku 4.5 by default). A keyless install is reported
    honestly (those seats cannot run) rather than making a live call without a
    key; when a key is present, a harmless read-only GET /v1/models validates it.
    The value is never echoed anywhere."""
    anthropic_seats = sorted(
        name for name in llm.SEATS
        if llm.resolve_seat(name, env).provider == "anthropic"
    )
    key = (env.get("ANTHROPIC_API_KEY") or "").strip()
    if not anthropic_seats:
        # Everything routes to openai (e.g. lanes overridden) — the key is not
        # needed. Say so; note it only if it happens to be set.
        if key:
            return [Result(INFO, "ANTHROPIC_API_KEY set but no seat currently "
                                 "routes to the Claude API lane — unused")]
        return [Result(INFO, "ANTHROPIC_API_KEY not needed — no seat routes to "
                             "the Claude API lane under the current seat map")]
    seats_txt = ", ".join(anthropic_seats)
    if not key:
        return [Result(
            FAIL,
            f"ANTHROPIC_API_KEY not set — the {seats_txt} seat(s) now run on the "
            "Claude API lane (Haiku 4.5) and cannot run without it; get one at "
            "console.anthropic.com/settings/keys, set a monthly cap, add to .env",
        )]
    req = urllib.request.Request(
        ANTHROPIC_MODELS_URL,
        headers={"x-api-key": key, "anthropic-version": llm.ANTHROPIC_VERSION,
                 "User-Agent": USER_AGENT},
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=ANTHROPIC_TIMEOUT_S) as resp:
            payload = json.load(resp)
        elapsed = time.monotonic() - started
        count = len(payload.get("data", []))
        return [Result(
            PASS,
            f"ANTHROPIC_API_KEY valid — read-only GET /v1/models OK "
            f"({count} models visible, {elapsed:.1f}s); powers the {seats_txt} "
            "seat(s)",
        )]
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return [Result(
                FAIL,
                f"ANTHROPIC_API_KEY rejected (HTTP {exc.code}) — mistyped or "
                "revoked; regenerate at console.anthropic.com/settings/keys and "
                "update .env",
            )]
        return [Result(
            FAIL,
            f"Anthropic check failed (HTTP {exc.code}) — key is present; retry in "
            "a minute or check status.anthropic.com",
        )]
    except Exception as exc:  # URLError / timeout / DNS — network-shaped failures
        return [Result(
            FAIL,
            f"could not reach api.anthropic.com ({type(exc).__name__}) — key is "
            "present but unverified; check network/VPN/proxy and re-run",
        )]


def check_perplexity_key(env: Dict[str, str]) -> List[Result]:
    key = (env.get("PERPLEXITY_API_KEY") or "").strip()
    # THE PAUSE (principal 2026-07-25) comes first, and it fires a PAID probe
    # for nobody. The doctor's job here changes shape: while discovery is
    # paused there is no key to validate for, so it REPORTS THE RULING instead
    # of nagging for a credential the product has decided not to use. A
    # key-shaped nag under a pause is how a paused feature quietly gets
    # un-paused by a helpful reader.
    if not config.discovery_enabled(env):
        held = (
            " A PERPLEXITY_API_KEY is present in the environment but unused — "
            "nothing here spends it; you can leave it or comment it out."
            if key else
            " No key needed, and none is being asked for."
        )
        return [
            Result(
                INFO,
                f"{config.DISCOVERY_PAUSE_REASON}. No probe was fired and "
                f"nothing was charged.{held}",
            )
        ]
    if not key:
        # M8 ruling: the principal DEFERRED this key by choice (RSS-only
        # discovery is the product's actual running state), so its absence
        # is information, not failure — a required-✗ here contradicted the
        # product and kept exit-0 unreachable on the real install. A set-
        # but-garbage value still fails below: a typo is an error, a
        # deferral is a decision.
        return [
            Result(
                INFO,
                "PERPLEXITY_API_KEY not set — deferred by choice; ingest runs "
                "RSS-only and says so. To add discovery later: "
                "perplexity.ai/settings/api → .env",
            )
        ]
    ping_file = paths.PROMPTS_DIR / "doctor_sonar_ping.txt"
    if not ping_file.exists():
        return [
            Result(
                FAIL,
                f"missing {_rel(ping_file)} — the checkout is incomplete; restore it "
                "from the repo",
            )
        ]
    try:
        ping_text = ping_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        # Same unguarded-read class as BUG-2: report, never traceback.
        return [
            Result(
                FAIL,
                f"{_rel(ping_file)} exists but is not readable ({exc}) — fix its "
                "file permissions; the key check needs it and was skipped",
            )
        ]
    body = json.dumps(
        {
            "model": "sonar",
            "messages": [{"role": "user", "content": ping_text}],
            # 16 is Perplexity's enforced minimum (HTTP 400 below it, found
            # live 2026-07-06 on the probe's first real contact — the key was
            # deferred through all of construction, so this line had never
            # touched the API).
            "max_tokens": 16,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        PERPLEXITY_CHAT_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=PERPLEXITY_TIMEOUT_S) as resp:
            json.load(resp)  # parse to confirm a real API response shape
        elapsed = time.monotonic() - started
        return [
            Result(
                PASS,
                f"PERPLEXITY_API_KEY valid — minimal sonar query OK ({elapsed:.1f}s; "
                "this check costs a fraction of a cent; the cron-reliability spike "
                "is a separate, still-pending gate)",
            )
        ]
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return [
                Result(
                    FAIL,
                    "PERPLEXITY_API_KEY rejected (401) — mistyped or revoked; "
                    "regenerate at perplexity.ai/settings/api and update .env",
                )
            ]
        return [
            Result(
                FAIL,
                f"Perplexity check failed (HTTP {exc.code}) — key is present; retry "
                "in a minute or check status.perplexity.ai",
            )
        ]
    except Exception as exc:
        return [
            Result(
                FAIL,
                f"could not reach api.perplexity.ai ({type(exc).__name__}) — key is "
                "present but unverified; check network/VPN/proxy and re-run",
            )
        ]


def check_optional_and_guards(env: Dict[str, str]) -> List[Result]:
    out: List[Result] = []

    if (env.get("GNEWS_API_KEY") or "").strip():
        out.append(
            Result(
                INFO,
                "GNEWS_API_KEY is set — noted; the GNews fallback path is not built "
                "and only triggers if the Sonar reliability spike fails",
            )
        )
    else:
        out.append(
            Result(
                INFO,
                "GNEWS_API_KEY not set — fine: optional fallback, deliberately "
                "ungranted unless the Sonar spike fails",
            )
        )

    # Validation itself lives in config (the single validator — BUG-1 was the
    # doctor's drifted copy of these rules). The doctor only decides how to
    # render: unset -> INFO with the documented default, valid -> PASS,
    # rejected -> FAIL quoting the validator's own message.
    if not (env.get("BUDGET_CAP_USD_PER_RUN") or "").strip():
        out.append(
            Result(
                INFO,
                # Display references the real default — a hardcoded display
                # literal is the same drift pattern that shipped BUG-1.
                f"BUDGET_CAP_USD_PER_RUN not set — default "
                f"{config.DEFAULT_BUDGET_CAP_USD_PER_RUN:.2f} USD/run applies "
                "(hard stop for a runaway generate run)",
            )
        )
    else:
        try:
            cap = config.budget_cap_usd_per_run(env)
            if cap > config.DEFAULT_BUDGET_CAP_USD_PER_RUN:
                out.append(Result(WARN, (
                    f"BUDGET_CAP_USD_PER_RUN = {cap:.2f} USD/run — above the "
                    f"{config.DEFAULT_BUDGET_CAP_USD_PER_RUN:.2f} recommended "
                    "default (M9 ruling 2026-07-06); consider lowering the pin "
                    "in your .env")))
            else:
                out.append(Result(PASS, f"BUDGET_CAP_USD_PER_RUN = {cap:.2f} USD/run"))
        except ValueError as exc:
            out.append(Result(FAIL, f"{exc} — fix it in .env"))

    # GENERATE_HOUR_LOCAL is DORMANT: v1 generation is on-demand only
    # (DECISIONS.md 2026-07-03) — nothing requires this var. Unset/valid are
    # informational; a set-but-garbage value still fails, because a typo in
    # .env is a config error regardless of whether anything reads it yet.
    if not (env.get("GENERATE_HOUR_LOCAL") or "").strip():
        out.append(
            Result(
                INFO,
                f"GENERATE_HOUR_LOCAL not set — fine (dormant: v1 is on-demand "
                f"only; default {config.DEFAULT_GENERATE_HOUR_LOCAL} "
                f"({config.DEFAULT_GENERATE_HOUR_LOCAL:02d}:00 local) would "
                "apply only if scheduling ever returns)",
            )
        )
    else:
        try:
            hour = config.generate_hour_local(env)
            out.append(
                Result(
                    PASS,
                    f"GENERATE_HOUR_LOCAL = {hour} ({hour:02d}:00 local) — noted, "
                    "but dormant: v1 generation is on-demand only",
                )
            )
        except ValueError as exc:
            out.append(Result(FAIL, f"{exc} — fix it in .env"))

    return out


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def check_database() -> List[Result]:
    from . import db  # stdlib-only module; safe to import unconditionally

    out: List[Result] = []

    # 1. Do the migrations apply cleanly? Validated on a scratch DB so the
    #    doctor never mutates real state.
    try:
        with tempfile.TemporaryDirectory(prefix="newslens-doctor-") as tmp:
            scratch = Path(tmp) / "scratch.db"
            db.migrate(db_path=scratch)
            con = db.connect(scratch)
            try:
                tables = [t for t in db.table_names(con) if t != "schema_migrations"]
            finally:
                con.close()
        out.append(
            Result(
                PASS,
                "migrations apply cleanly to a scratch DB — tables: "
                + ", ".join(tables),
            )
        )
    except Exception as exc:
        out.append(
            Result(
                FAIL,
                f"migrations failed on a scratch DB ({type(exc).__name__}: {exc}) — "
                "the schema itself is broken; this needs a code fix, not a config fix",
            )
        )
        return out

    # 2. Is the data directory writable? (Cheap early catch for the cron era.)
    #    This probe is the doctor's ONE deliberate write to real state — you
    #    cannot verify writability without writing. It cleans up after itself
    #    and never touches the database file.
    #
    #    Stage-0 M1, QA fix loop 1 — DEFENCE IN DEPTH at the minting site.
    #    `mkdir(parents=True)` will happily bring a whole profile world into
    #    existence, and that is how a typo'd NEWSLENS_PROFILE became a real
    #    profile that every later verb then accepted. doctor.main and cli.main
    #    both refuse unknown profiles now; this makes the refusal structural,
    #    so a future entrypoint that forgets require_exists still cannot mint
    #    a reader. Scoped precisely: only when the probe target actually lives
    #    inside the missing profile root. Under a path redirection the fence
    #    stands down (the target is sandbox by the seam's law) and the probe
    #    then writes wherever the redirection points — including, if an
    #    operator deliberately aims it inside the real profiles/ namespace,
    #    minting a directory require_exists will later accept. That shape is
    #    the operator's own hand (mkdir -p equivalent), disclosed rather than
    #    guarded: treating redirected targets as real state would invert the
    #    seam. (Gate FIX-1, QA charge-2 shapes X/Y/Z/W on the M1 record.)
    _profile = paths.current_profile()
    if _profile != paths.DEFAULT_PROFILE:
        _root = paths.profile_root(_profile)
        if not _root.is_dir() and _root in paths.DATA_DIR.parents:
            out.append(
                Result(
                    FAIL,
                    f"profile {_profile!r} does not exist ({_root}) — nothing "
                    "was created. Check --profile/NEWSLENS_PROFILE for a typo, "
                    f"or run: newslens profile create {_profile}",
                )
            )
            return out
    try:
        paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe = paths.DATA_DIR / ".doctor-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        out.append(Result(PASS, f"data directory writable ({_rel(paths.DATA_DIR)}/)"))
    except OSError as exc:
        out.append(
            Result(
                FAIL,
                f"data directory not writable ({exc}) — fix permissions on "
                f"{paths.DATA_DIR}",
            )
        )
        return out

    # 3. State of the real database — strictly read-only: pending_migrations
    #    opens mode=ro and never creates the file, a dir, or a table (QA fix
    #    loop 1: a health check must not mutate what it diagnoses).
    if not paths.DB_PATH.exists():
        out.append(
            Result(
                WARN,
                f"{_rel(paths.DB_PATH)} not created yet — run: newslens migrate",
            )
        )
    else:
        try:
            pending = db.pending_migrations()
            if pending:
                out.append(
                    Result(
                        FAIL,
                        f"{_rel(paths.DB_PATH)} is behind by {len(pending)} "
                        f"migration(s) ({', '.join(pending)}) — run: newslens migrate",
                    )
                )
            else:
                out.append(Result(PASS, f"{_rel(paths.DB_PATH)} present and up to date"))
        except sqlite3.DatabaseError as exc:
            out.append(
                Result(
                    FAIL,
                    f"{_rel(paths.DB_PATH)} exists but is unreadable ({exc}) — if "
                    "it's corrupt, move it aside and re-run: newslens migrate",
                )
            )
        out.extend(check_pool_capacity())

    return out


def check_pool_capacity(limit: int = 10) -> List[Result]:
    """Is the ranking pool losing items to the cap? (NL-142 item 2.)

    THE THING THIS EXISTS TO PREVENT: 27 of 48 ranking runs sat at
    item_count=550 — the cap binding on more than half of all runs — and
    nothing ever said so out loud. A capacity derate that only shows up in a
    JSON blob nobody reads is a silent derate. The doctor is where a reader
    looks when something feels off, so the doctor answers it.

    Strictly read-only (mode=ro URI, same law as every other real-DB probe in
    this file), and every failure degrades to silence: an old DB with no
    `pool` key in meta, a missing table, an unparseable blob — none of those
    are the reader's problem, and none of them may turn into a doctor FAIL."""
    from . import db, ranking

    out: List[Result] = []
    try:
        con = db.connect_readonly()
    except sqlite3.Error:
        return out
    try:
        try:
            rows = con.execute(
                "SELECT meta FROM ranking_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        except sqlite3.Error:
            return out  # table absent on an older schema — nothing to report
        capped = 0
        seen = 0
        newest: Optional[dict] = None
        for row in rows:
            try:
                meta = json.loads(row["meta"] or "{}")
            except (ValueError, TypeError):
                continue
            pool = meta.get("pool")
            if not isinstance(pool, dict):
                # Pre-NL-142 run: item_count is the only signal it left behind,
                # and "== the cap" is the best inference available from it.
                count = meta.get("item_count")
                if isinstance(count, int):
                    seen += 1
                    if count >= ranking.MAX_INPUT_ITEMS:
                        capped += 1
                continue
            seen += 1
            if pool.get("evicted"):
                capped += 1
            if newest is None:
                newest = pool
        if not seen:
            return out
        if newest and newest.get("evicted"):
            named = ", ".join(
                f"{o} ({n})" for o, n in (newest.get("evicted_by_outlet") or [])[:3]
            )
            out.append(
                Result(
                    WARN,
                    f"ranking pool: last run had {newest.get('window_total')} "
                    f"candidates against a {ranking.MAX_INPUT_ITEMS}-item cap — "
                    f"{newest.get('evicted')} evicted before ranking "
                    f"({named}). Eviction follows sources.yaml order, so the "
                    f"top of your file loses its day first",
                )
            )
        if capped:
            out.append(
                Result(
                    WARN,
                    f"ranking pool: {capped} of the last {seen} run(s) lost items "
                    f"to the {ranking.MAX_INPUT_ITEMS}-item cap — a cap binding "
                    f"this often is a capacity decision, not an edge case",
                )
            )
        else:
            out.append(
                Result(
                    PASS,
                    f"ranking pool: none of the last {seen} run(s) hit the "
                    f"{ranking.MAX_INPUT_ITEMS}-item cap",
                )
            )
    finally:
        con.close()
    return out


# ---------------------------------------------------------------------------
# Sources & interests
# ---------------------------------------------------------------------------

# NL-142 item 3: how much of each feed the doctor reads to date it. The
# shape sniff below only ever needed the first few hundred bytes; the
# staleness check needs actual entries. 64KB covers the newest several items
# of every feed measured in the slate probes while keeping this ONE round
# trip per feed — no second fetch, no full download of a 1MB feed.
STALE_SNIFF_BYTES = 65536


def _feed_age_result(source_name: str, head: bytes) -> Optional[Result]:
    """The doctor-grade half of the staleness tooth (gate charter R-E).

    A frozen archive answers HTTP 200 with valid RSS, so `check_feed_urls`
    passes it and the ingest zero-entry warning never fires. This dates the
    feed and says so. Returns None when there is nothing to report — a healthy
    fresh feed adds no line, so the doctor's output does not grow by 65 rows.

    Reads the SAME prefix bytes the shape check already fetched. feedparser
    tolerates a truncated document (it yields the entries it got), and we take
    the MAX published_at over whatever parsed — so a feed ordered oldest-first
    is judged on the newest date actually seen, never on position.

    Failure is silent BY DESIGN: this is a bonus verdict riding a fetch that
    already produced its own PASS/WARN/FAIL. A parser hiccup here must not
    turn a reachable feed into a doctor failure."""
    from . import ingest

    try:
        items, _ = ingest.parse_entries(head)
        staleness = ingest.feed_staleness(items)
    except Exception:
        return None
    if not items:
        return None
    if staleness["dateless"]:
        # Exempt, and told the truth about rather than passed over in silence.
        return Result(
            INFO,
            f"{source_name}: feed publishes no item dates — exempt from the "
            f"staleness check (its items are still fully eligible; only the "
            f"corpus dateline is omitted)",
        )
    age = staleness["age_days"]
    if age is None or age <= ingest.STALE_FEED_DAYS:
        return None
    return Result(
        WARN,
        f"{source_name}: newest item is {age:.0f} days old (published "
        f"{str(staleness['newest'])[:10]}) — the feed resolves and parses, but "
        f"it may be a frozen archive. Threshold is "
        f"{ingest.STALE_FEED_DAYS}d; check the rss_url or disable the source",
    )


def check_feed_urls(sources) -> List[Result]:
    from . import net  # shared 308-following opener + pipeline UA: the doctor
    # must see exactly what ingestion sees (M2 review carryover)

    out: List[Result] = []
    for source in sources:
        started = time.monotonic()
        try:
            head, status = net.head_bytes(
                source.rss_url, timeout=FEED_TIMEOUT_S, n=STALE_SNIFF_BYTES
            )
            elapsed = time.monotonic() - started
            if any(marker in head for marker in (b"<rss", b"<feed", b"<?xml", b"<rdf")):
                out.append(
                    Result(
                        PASS,
                        f"feed resolves: {source.name} (HTTP {status}, {elapsed:.1f}s)",
                    )
                )
                # NL-142 item 3: date the feed off the bytes we already have.
                # Only speaks when there is something to say.
                age_result = _feed_age_result(source.name, head)
                if age_result is not None:
                    out.append(age_result)
            else:
                out.append(
                    Result(
                        WARN,
                        f"{source.name}: URL responds (HTTP {status}) but does not "
                        "look like an RSS/Atom feed — double-check rss_url",
                    )
                )
        except Exception as exc:
            reason = getattr(exc, "reason", None) or exc
            out.append(
                Result(
                    FAIL,
                    f"{source.name}: feed URL failed to resolve "
                    f"({type(exc).__name__}: {reason}) — check the URL and your network",
                )
            )
    return out


def check_sources() -> List[Result]:
    if not paths.SOURCES_FILE.exists():
        return [
            Result(
                FAIL,
                "sources.yaml is missing — restore the template from the repo, then "
                "add your outlets",
            )
        ]
    try:
        cfg = config.load_sources()
    except ImportError:
        # config itself imports fine pre-install; load_sources imports yaml
        # lazily, so a missing PyYAML surfaces here, at the call.
        return [
            Result(
                WARN,
                "sources.yaml validation skipped (PyYAML not installed — see the "
                "missing-deps line above)",
            )
        ]
    except config.SourcesParseError as exc:
        # Covers missing-at-read, unreadable (BUG-2), and malformed YAML alike.
        return [Result(FAIL, f"{exc} — fix sources.yaml (the template comments show the format)")]

    out: List[Result] = []
    for problem in cfg.problems:
        out.append(Result(FAIL, f"sources.yaml: {problem}"))

    # NL-17 M3 — THE YAML-SIDE XOR DOOR, reported here because the doctor is
    # this system's degrade-loud surface. WARN and never FAIL: his file
    # legitimately carries a tag whose concept is mid-move, and `steering.derive`
    # — not this line — decides whether that tag still scores (a moved concept's
    # tag is suppressed only once its entity is actually weight-bearing). A FAIL
    # here would brick a lawful state, which is Rook's whole dissent (ENG :119):
    # degrade-loud, never a dead run, and never a meta-only disclosure either.
    try:
        from . import db as _db, vocab_move
        _con = _db.connect_readonly()
        try:
            for hit in vocab_move.yaml_door_collisions(_con, cfg):
                out.append(Result(
                    WARN,
                    f"sources.yaml: `{hit['tag']}` ({hit['level']} tag) names a "
                    f"followed entity ({hit['entity']}) — one concept, two "
                    f"vocabularies. Propose the move with "
                    f"`scripts/nl17-vocabulary-move`; until it is blessed the "
                    f"ledger decides which one scores."))
        finally:
            _con.close()
    except Exception:                                        # noqa: BLE001
        # A door that can kill the doctor is not a door. No record yet, no
        # migration yet, no PyYAML — all of them mean "nothing to say here".
        pass

    if cfg.has_active_sources:
        fetchable = cfg.fetchable_sources
        out.append(
            Result(
                PASS,
                f"sources.yaml parses — {len(fetchable)} active source(s) configured",
            )
        )
        for warning in cfg.warnings:
            out.append(Result(WARN, f"sources.yaml: {warning}"))
        if cfg.reference_only_sources:
            names = ", ".join(s.name for s in cfg.reference_only_sources)
            out.append(
                Result(
                    INFO,
                    f"{len(cfg.reference_only_sources)} reference-only outlet(s) — "
                    f"citable, never fetched: {names}",
                )
            )
        if cfg.disabled_sources:
            names = ", ".join(s.name for s in cfg.disabled_sources)
            out.append(
                Result(
                    INFO,
                    f"{len(cfg.disabled_sources)} source(s) present but disabled: {names}",
                )
            )
        out.extend(check_feed_urls(fetchable))
    else:
        out.append(
            Result(
                WARN,
                config.NO_ACTIVE_SOURCES_MSG
                + " (the template comments in the file show the format; needed "
                "before milestone 2 can ingest anything)",
            )
        )
        out.append(Result(INFO, "no active RSS feeds to resolve yet"))

    out.append(
        Result(
            INFO,
            "settings: threads_steer_selection = "
            + ("true (threads boost selection)" if cfg.threads_steer_selection
               else "false (A6 default: threads are recorded and woven into "
               "continuity, but selection is tags + world impact only)"),
        )
    )
    if cfg.has_interests:
        out.append(
            Result(
                PASS,
                f"interests configured — {len(cfg.interests_broad)} broad, "
                f"{len(cfg.interests_granular)} granular",
            )
        )
    else:
        out.append(Result(WARN, config.NO_INTERESTS_MSG))

    return out


# ---------------------------------------------------------------------------
# TTS engine (M6 — a REAL local synthesis, not a liveness ping; the
# engineering-2 ruling. QA seam: gate the real synth behind the engine
# actually being installed + NEWSLENS_DOCTOR_TTS_SYNTH != "0".)
# ---------------------------------------------------------------------------

def check_tts() -> List[Result]:
    from . import audio, config as cfg_mod

    out: List[Result] = []
    try:
        engine = cfg_mod.load_sources().tts_engine
    except Exception:  # sources problems are reported by check_sources
        engine = audio.DEFAULT_TTS_ENGINE
    problem = audio.kokoro_ready()
    if engine == "openai":
        out.append(Result(INFO, "settings.tts_engine = openai (gpt-4o-mini-tts, "
                                "~$0.015/min on the OpenAI key) — an EXPLICIT "
                                "pin, and the principal's ear-test pick (voice "
                                "ruling 2026-07-06). The code default is kokoro "
                                "($0) per the $0-run law (2026-07-25)"))
        if problem:
            out.append(Result(INFO, f"local kokoro engine not installed ({problem}) "
                                    "— fine while the openai engine is selected"))
        return out
    # engine == "kokoro" is the DEFAULT path (NL-96 / the $0-run law,
    # 2026-07-25) as well as any explicit kokoro pin — and this branch cannot
    # distinguish the two, because config resolves the default before the
    # doctor ever sees it. So NOTHING here may nudge toward the paid engine:
    # against a law that says a run costs $0, that advice would be wrong for
    # the principal (whose pin is law-compliant) and wrong for every no-pin
    # fresh profile. A law-compliant default state is not warn-worthy — INFO.
    # The doctor NEVER edits sources.yaml for the principal.
    out.append(Result(INFO, (
        "settings.tts_engine = kokoro (local, $0/episode) — the code default "
        "per the $0-run law (2026-07-25). The 2026-07-06 ear test preferred "
        "the openai VOICE (gpt-4o-mini-tts, ~$0.015/min); one explicit "
        "`settings.tts_engine: openai` line in sources.yaml selects it")))
    if problem:
        # Same shape-2 correction: "pinned in sources.yaml" was false on the
        # default path. Name the SELECTED engine, claim nothing about how it
        # got selected.
        out.append(Result(FAIL, f"tts (kokoro, the selected engine): {problem}"))
        return out
    if os.environ.get("NEWSLENS_DOCTOR_TTS_SYNTH") == "0":
        out.append(Result(INFO, "tts real-synthesis check skipped "
                                "(NEWSLENS_DOCTOR_TTS_SYNTH=0 — QA/offline mode)"))
        return out
    import tempfile
    from pathlib import Path as _P
    try:
        with tempfile.TemporaryDirectory(prefix="newslens-tts-") as tmp:
            res = audio.generate_audio(
                "NewsLens doctor check: the local voice is working.",
                _P(tmp) / "doctor.wav", engine="kokoro",
            )
        rate = res.detail.get("realtime_x")
        out.append(Result(
            PASS,
            f"tts kokoro: REAL synthesis OK — {res.duration_s:.1f}s of audio in "
            f"{res.gen_time_s:.1f}s"
            + (f" ({rate}x realtime; this machine measured ~4.4x on a full "
               "script — below the reconvene's 14x floor, flagged at M6)" if rate else ""),
        ))
    except audio.AudioError as exc:
        out.append(Result(FAIL, f"tts kokoro: real synthesis FAILED — {exc}"))
    return out


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------

def _parse_claude_version(text: str) -> Tuple[int, ...]:
    """Extract the (major, minor, patch) tuple from `claude --version` output
    (e.g. '2.1.212 (Claude Code)'). Returns () if unparseable."""
    import re
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(g) for g in m.groups()) if m else ()


def check_subscription_lane(env: Dict[str, str]) -> List[Result]:
    """The `claude -p` subscription lane (B3): binary resolution + version, for
    the seats that resolve to the subscription lane under the current config
    (rank/editor/script by default). subscription is ALWAYS the priority; the
    api lane is the registered fall-over.

    Resolution order is NEWSLENS_CLAUDE_BIN -> PATH -> ~/.local/bin/claude — the
    doctor reports WHICH resolved. A missing binary is a FAIL naming the install
    fix (those seats can't run their default lane). `claude --version` is
    recorded (read-only: no prompt, no spend) and checked against the effort
    floor. The AUTH state is NOT probed by default: a real `claude -p` ping
    SPENDS the principal's subscription quota, so it is opt-in only (see the
    NEWSLENS_DOCTOR_SUBSCRIPTION_PROBE design note below), never auto-fired."""
    sub_seats = sorted(
        name for name in llm.SEATS
        if llm.resolve_seat(name, env).lane == "subscription"
    )
    if not sub_seats:
        return [Result(INFO, "no seat resolves to the claude -p subscription "
                             "lane under the current config — nothing to check "
                             "(the api lane covers the anthropic seats)")]
    seats_txt = ", ".join(sub_seats)
    bin_path, source = llm.resolve_claude_bin(env)
    if bin_path is None:
        return [Result(
            FAIL,
            f"claude CLI NOT found — the {seats_txt} seat(s) default to the "
            f"subscription lane and cannot run: {source}. Install the CLI and "
            "run `claude` once to log in (SETUP.md), or flip these seats to the "
            "api fall-over (NEWSLENS_LANE_<SEAT>=api, needs ANTHROPIC_API_KEY)",
        )]
    out: List[Result] = [Result(
        INFO,
        f"claude CLI resolved via {source}: {bin_path} — powers the {seats_txt} "
        "seat(s) on the subscription lane",
    )]
    # Record the version (read-only; no prompt, no spend). A stripped-ish env is
    # unnecessary for --version, but a tight timeout + captured failure path is
    # (ENGINEERING: every external call has a timeout + a visible failure path).
    try:
        proc = subprocess.run(
            [bin_path, "--version"], capture_output=True, text=True,
            stdin=subprocess.DEVNULL,        # B3-D4: never inherit/block on the doctor's stdin
            timeout=CLAUDE_VERSION_TIMEOUT_S,
        )
        ver_txt = (proc.stdout or proc.stderr or "").strip()
        ver = _parse_claude_version(ver_txt)
        if not ver:
            out.append(Result(WARN, f"claude --version returned unparseable "
                                    f"output ({ver_txt[:60]!r}) — CLI present "
                                    "but version unknown"))
        elif ver < CLAUDE_VERSION_FLOOR:
            floor = ".".join(str(n) for n in CLAUDE_VERSION_FLOOR)
            out.append(Result(WARN, f"claude {ver_txt} is below the v{floor} "
                                    "effort-control floor — the subscription "
                                    "lane runs, but effort-bearing seats can't "
                                    "map effort (upgrade the CLI)"))
        else:
            out.append(Result(PASS, f"claude {ver_txt} (>= effort floor "
                                    f"v{'.'.join(str(n) for n in CLAUDE_VERSION_FLOOR)})"))
    except Exception as exc:  # timeout / OSError — CLI present but not runnable
        out.append(Result(FAIL, f"claude --version failed ({type(exc).__name__}: "
                                f"{exc}) — the CLI at {bin_path} is present but "
                                "not runnable; re-install or fix permissions"))
    # The OPTIONAL live auth probe — DESIGNED, not fired. A real `claude -p`
    # single-token ping is the only way to prove the CLI is LOGGED IN (the
    # binary + version pass without auth), but it SPENDS subscription quota, so
    # it is opt-in and skippable, never part of the default run.
    if (env.get("NEWSLENS_DOCTOR_SUBSCRIPTION_PROBE") or "").strip() == "1":
        out.append(Result(
            WARN,
            "NEWSLENS_DOCTOR_SUBSCRIPTION_PROBE=1 requested a LIVE probe — this "
            "would spend subscription quota. The recommended shape: one "
            "`claude -p --output-format json --model claude-haiku-4-5` with a "
            "1-token prompt ('ok') on stdin, is_error=false => authed. NOT "
            "fired here — the live smoke is the principal's to run manually "
            "(SETUP.md), so the default doctor never spends. Unset the flag.",
        ))
    else:
        out.append(Result(
            INFO,
            "auth NOT probed (a live `claude -p` ping spends subscription "
            "quota) — the binary+version pass above does not prove the CLI is "
            "logged in; run `claude` once interactively to confirm login "
            "(SETUP.md). Opt into the probe design with "
            "NEWSLENS_DOCTOR_SUBSCRIPTION_PROBE=1",
        ))
    return out


def check_llm_lanes(env: Dict[str, str]) -> List[Result]:
    """The provider-seam lane map: one line per seat showing the resolved
    provider/model/lane + per-seat price, plus fallback state. Current stack
    (post item C, 2026-07-17): the anthropic content seats all DEFAULT to the
    SUBSCRIPTION lane — rank/editor/script/state Haiku, the writer Opus, the
    analyst Sonnet — with the api lane as each one's registered fall-over;
    synthesis is the lone gpt-4o/api seat. A seat resolved (via NEWSLENS_LANE /
    NEWSLENS_LANE_<SEAT>) to a lane with no registered provider is flagged
    FAIL here — the same fail-loud condition the run itself hits."""
    out: List[Result] = []
    for name in llm.SEATS:
        cfg = llm.resolve_seat(name, env)
        line = (f"{name}: {cfg.provider}/{cfg.model} · lane={cfg.lane} · "
                f"timeout {cfg.timeout_s}s · "
                f"${cfg.usd_per_mtok_in:.2f}/${cfg.usd_per_mtok_out:.2f} per MTok")
        try:
            llm.check_lane(cfg)  # preflight only — no live call
            out.append(Result(INFO, line))
        except llm.LaneUnavailable as exc:
            out.append(Result(FAIL, f"{line} — {exc}"))
    if llm.fallback_armed(env):
        out.append(Result(
            WARN,
            "NEWSLENS_LANE_FALLBACK=api ARMED — a subscription-lane seat that "
            "goes unavailable (missing/unauthed CLI, quota) falls ONCE to the "
            "api lane, labeled lane=api(fallback:…) in the ledger. This spends "
            "real API money the subscription lane would not — armed by you",
        ))
    else:
        out.append(Result(
            INFO,
            "fallback unarmed (fail-loud default) — a subscription-lane seat "
            "that goes unavailable KILLS the run naming the fix, rather than "
            "silently spending API money. Set NEWSLENS_LANE_FALLBACK=api to opt "
            "in (the ship checkpoint asks whether to arm it)",
        ))
    out.append(Result(
        INFO,
        "registered lanes: openai/api, anthropic/api, anthropic/subscription "
        "(the claude -p lane, B3) — the anthropic content seats (rank/editor/"
        "script/state Haiku, writer Opus, analyst Sonnet) DEFAULT to subscription "
        "with api as their fall-over (item C, 2026-07-17 — field-proven edition "
        "7); synthesis is the lone gpt-4o/api seat",
    ))
    return out


def cost_estimate() -> List[Result]:
    # FIX-4 (B4, NEW-1): the figures DERIVE from llm.SEATS so this prose can never
    # drift from the seat table again (the prior text claimed cost was DROPPING —
    # a lie of summary once the content seats flipped up to Opus/Sonnet).
    w = llm.SEATS["writer"]
    a = llm.SEATS["analyst"]
    cap = config.DEFAULT_BUDGET_CAP_USD_PER_RUN
    return [
        Result(
            INFO,
            "item C (2026-07-17): the writer "
            f"({w.model}, ${w.usd_per_mtok_in:.2f}/${w.usd_per_mtok_out:.2f} per "
            "MTok, adaptive thinking billed as output) and the analyst "
            f"({a.model}, ${a.usd_per_mtok_in:.2f}/${a.usd_per_mtok_out:.2f}) "
            "joined rank/editor/script/state on the SUBSCRIPTION lane — so a "
            "default edition's CHARGED cost is ~$0 (usd_charged 0.0; field-proven "
            "edition 7). The SHADOW (API-priced compute, what the EDITION cap "
            "guards; the battery's own gate bounds charged dollars) is "
            f"~$0.90-1.30/edition; the budget cap defaults to "
            f"${cap:.2f}/run (the shadow is UNDISCOUNTED — the cap OVER-counts, "
            "the safe direction for a money guard). The exact figure is MEASURED "
            "at the first real edition + the ~07-24 battery, never assumed. "
            "Audio on the DEFAULT TTS adds $0 — kokoro runs locally (the $0-run "
            "law, 2026-07-25). Pin settings.tts_engine: openai and audio adds "
            "~$0.07/run — gpt-4o-mini-tts at ~$0.015/min, measured $0.067 on a "
            "4.4-min episode (the 2026-07-06 ear-test ruling, on voice). Real "
            "per-step costs land in briefings.token_cost on every "
            "generate (per-seat model/lane/shadow keys)",
        ),
        # HONESTY LINE (2026-07-25/26). "~$0 charged" is true of the LLM seats
        # and, since the pause, of tier-2 discovery. It is NOT the whole run:
        # analysis verification still makes one METERED Sonar call per
        # depth-tier story whenever PERPLEXITY_API_KEY is set. The pause ruling
        # named discovery, the doctor probe and sonar_spike — not this caller —
        # so it is reported here rather than silently changed.
        Result(
            INFO,
            "Metered spend outside the subscription, complete list: tier-2 "
            "discovery is PAUSED ($0). Analysis VERIFICATION is not — "
            "`analyze`/`generate` still make one Sonar call per depth-tier "
            "story when PERPLEXITY_API_KEY is set (logged as analysis_usd; "
            "~$0.003/edition on the 2026-07-25 run). Comment the key out of "
            ".env and every Sonar path in the product is cold",
        ),
    ]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def run_doctor() -> int:
    print(f"NewsLens doctor · {datetime.now().strftime('%Y-%m-%d %H:%M %Z').strip()}")
    print(f"project: {paths.PROJECT_ROOT}")
    # Stage-0 M1: say WHOSE world was checked. Every line below — database,
    # sources, cost, TTS — is about this profile and no other; a report that
    # did not name it would be read as the founder's by default.
    #
    # NL-98 (doctor guard-awareness) slots into check_database() below: one
    # more Result comparing the live memory.md's stamp to this profile's
    # sync_state row ("memory.md in sync (gen N)" / "STALE — syncs
    # degrading"). Deliberately NOT built here — it is its own tracker row
    # with its own QA leg. The profile line is its natural neighbour.
    _profile = paths.current_profile()
    print(f"profile: {_profile}"
          + ("  (founder / default — data/, memory.md, sources.yaml in place)"
             if _profile == paths.DEFAULT_PROFILE
             else f"  (profiles/{_profile}/)"))

    sections: List[Tuple[str, List[Result]]] = []

    env_results = check_python() + check_deps() + check_checkout()
    sections.append(("Environment", env_results))

    env, env_notes = load_effective_env()
    key_results = (
        env_notes
        + check_openai_key(env)
        + check_anthropic_key(env)
        + check_perplexity_key(env)
        + check_optional_and_guards(env)
    )
    sections.append(("Config & keys", key_results))

    sections.append(("Database", check_database()))
    sections.append(("Sources & interests", check_sources()))
    sections.append(("TTS engine", check_tts()))
    sections.append(("LLM lanes", check_llm_lanes(env)))
    sections.append(("Subscription lane (claude -p)", check_subscription_lane(env)))
    sections.append(("Cost", cost_estimate()))

    tally = {PASS: 0, FAIL: 0, WARN: 0, INFO: 0}
    for title, results in sections:
        print(f"\n{title}")
        for r in results:
            tally[r.status] += 1
            print(f"  {r.status} {r.text}")

    print(
        f"\nSummary: {tally[FAIL]} required failing · {tally[WARN]} warnings · "
        f"{tally[PASS]} passing"
    )
    if tally[FAIL]:
        print("Doctor exit 1 — fix the ✗ lines above (each one says how).")
        return 1
    if tally[WARN]:
        print("Doctor exit 0 — everything required passes; the ⚠ lines are worth a look.")
        return 0
    print("Doctor exit 0 — all checks pass. NewsLens is healthy.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    paths.allow_real_paths()  # the real entrypoint (incident guard, 2026-07-14)
    # Stage-0 M1: scripts/doctor is the pre-install entrypoint, so it takes the
    # same --profile the CLI does (NEWSLENS_PROFILE works too). Hand-parsed:
    # argparse here would change the doctor's one-flagless-command contract for
    # every other invocation.
    args = list(sys.argv[1:] if argv is None else argv)
    if "--profile" in args:
        i = args.index("--profile")
        if i + 1 >= len(args):
            print("--profile needs a name", file=sys.stderr)
            return 2
        name = args[i + 1]
        del args[i:i + 2]
    else:
        name = next((a.split("=", 1)[1] for a in args
                     if a.startswith("--profile=")), None)
        args = [a for a in args if not a.startswith("--profile=")]
    if args:
        print(f"unknown doctor argument(s): {' '.join(args)} — the doctor takes "
              "only --profile NAME", file=sys.stderr)
        return 2
    # QA fix loop 1 (F1/F2): resolve the profile the SAME way cli.main does —
    # ALWAYS, however it was selected. The earlier version validated only the
    # --profile flag, so NEWSLENS_PROFILE=ghost (the documented shell/launchd
    # shape) walked past every check and the writability probe below MINTED
    # profiles/ghost/data — after which require_exists sees a directory and
    # every other verb accepts the typo too. set_profile(None) also CLEARS a
    # pin left by an earlier in-process main(), which is the doctor's half of
    # the same leak the CLI had.
    from . import profiles
    try:
        active_profile = paths.set_profile(name)
        if active_profile != paths.DEFAULT_PROFILE:
            profiles.require_exists(active_profile)
    except (paths.ProfileError, profiles.ProfileMissingError) as exc:
        # Malformed names arrive here too (current_profile raises on a bad
        # env value): a boundary refusal with the CLI's copy, never a
        # traceback out of a health check.
        print(f"profile: {exc}", file=sys.stderr)
        return 2
    for line in profiles.redirection_warnings(active_profile):
        print(f"warning: {line}", file=sys.stderr)
    return run_doctor()


if __name__ == "__main__":
    sys.exit(main())
