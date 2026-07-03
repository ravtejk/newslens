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
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config, paths  # config is stdlib-only at import time (lazy yaml)

PASS = "✓"
FAIL = "✗"
WARN = "⚠"
INFO = "○"

OPENAI_MODELS_URL = "https://api.openai.com/v1/models"
PERPLEXITY_CHAT_URL = "https://api.perplexity.ai/chat/completions"
OPENAI_TIMEOUT_S = 15
PERPLEXITY_TIMEOUT_S = 20
FEED_TIMEOUT_S = 10
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
    key = (env.get("OPENAI_API_KEY") or "").strip()
    if not key:
        return [
            Result(
                FAIL,
                "OPENAI_API_KEY not set — get one at platform.openai.com/api-keys, "
                "then add to .env",
            )
        ]
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
                f"({count} models visible, {elapsed:.1f}s)",
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


def check_perplexity_key(env: Dict[str, str]) -> List[Result]:
    key = (env.get("PERPLEXITY_API_KEY") or "").strip()
    if not key:
        return [
            Result(
                FAIL,
                "PERPLEXITY_API_KEY not set — get one at perplexity.ai/settings/api, "
                "then add to .env",
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
            "max_tokens": 8,
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
                "BUDGET_CAP_USD_PER_RUN not set — default 0.50 USD/run applies "
                "(hard stop for a runaway generate run)",
            )
        )
    else:
        try:
            cap = config.budget_cap_usd_per_run(env)
            out.append(Result(PASS, f"BUDGET_CAP_USD_PER_RUN = {cap:.2f} USD/run"))
        except ValueError as exc:
            out.append(Result(FAIL, f"{exc} — fix it in .env"))

    if not (env.get("GENERATE_HOUR_LOCAL") or "").strip():
        out.append(
            Result(
                INFO,
                "GENERATE_HOUR_LOCAL not set — default 6 (06:00 local) applies; "
                "scheduling itself lands at milestone 7",
            )
        )
    else:
        try:
            hour = config.generate_hour_local(env)
            out.append(Result(PASS, f"GENERATE_HOUR_LOCAL = {hour} ({hour:02d}:00 local)"))
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

    return out


# ---------------------------------------------------------------------------
# Sources & interests
# ---------------------------------------------------------------------------

def check_feed_urls(sources) -> List[Result]:
    out: List[Result] = []
    for source in sources:
        req = urllib.request.Request(
            source.rss_url, headers={"User-Agent": USER_AGENT}
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=FEED_TIMEOUT_S) as resp:
                head = resp.read(4096)
                status = resp.getcode()
            elapsed = time.monotonic() - started
            if any(marker in head for marker in (b"<rss", b"<feed", b"<?xml", b"<rdf")):
                out.append(
                    Result(
                        PASS,
                        f"feed resolves: {source.name} (HTTP {status}, {elapsed:.1f}s)",
                    )
                )
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

    if cfg.has_active_sources:
        out.append(
            Result(
                PASS,
                f"sources.yaml parses — {len(cfg.sources)} active source(s) configured",
            )
        )
        out.extend(check_feed_urls(cfg.sources))
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
# Cost
# ---------------------------------------------------------------------------

def cost_estimate() -> List[Result]:
    return [
        Result(
            INFO,
            "estimated cost-per-run ~$0.18 (~$5.50/month at daily cadence) — "
            "static estimate from the approved spec §C; ~97% of it is TTS, whose "
            "vendor is under re-evaluation before milestone 6 (may drop to ~$0 if "
            "a local model is chosen); real per-run cost logging arrives with the "
            "pipeline",
        )
    ]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def run_doctor() -> int:
    print(f"NewsLens doctor · {datetime.now().strftime('%Y-%m-%d %H:%M %Z').strip()}")
    print(f"project: {paths.PROJECT_ROOT}")

    sections: List[Tuple[str, List[Result]]] = []

    env_results = check_python() + check_deps() + check_checkout()
    sections.append(("Environment", env_results))

    env, env_notes = load_effective_env()
    key_results = (
        env_notes
        + check_openai_key(env)
        + check_perplexity_key(env)
        + check_optional_and_guards(env)
    )
    sections.append(("Config & keys", key_results))

    sections.append(("Database", check_database()))
    sections.append(("Sources & interests", check_sources()))
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


def main() -> int:
    return run_doctor()


if __name__ == "__main__":
    sys.exit(main())
