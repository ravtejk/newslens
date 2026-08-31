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
import re
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
# NL-160: the OPT-IN live auth probe's own window. Wider than --version because
# this one pays CLI startup + the agentic harness (the same tax that forced the
# lane's separate timeout_sub_s), and far tighter than any seat's, because the
# probe's whole prompt is the word "ok". An auth REJECTION comes back in about a
# second — the window is sized for the healthy case, not the failing one.
CLAUDE_PROBE_TIMEOUT_S = 60
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
    env — some LIVE seat resolves to the OpenAI provider (gpt-4o). Today that is
    NO seat at all: `synthesis` is the only openai row left and it is dormant
    (no live call site), so this check's not-needed branch is the one a default
    install takes. (This said "that is only the state/memory seat (and
    synthesis...)" until NL-155, 2026-08-14 — ENG-M0 moved `state` to Opus 4.8
    on the subscription lane 2026-08-06 and the sentence outlived it.) All seven
    of rank/editor/script/analyst/writer/state/follow_altitude are anthropic and
    the OpenAI key is INERT for them. So a keyless install with all-anthropic content seats is
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


# ---------------------------------------------------------------------------
# Derived prose — the seat map is READ, never retyped
#
# NL-147, and the reason these three one-line helpers exist at all: this file
# carried FOUR separate hand-written sentences naming rank/editor/script as
# "Haiku 4.5" seats, and every one of them was still saying it two seat batches
# after ENG-M0 (2026-08-02/06) moved those seats to Sonnet 5 / Opus 4.8 — because
# each was a sentence somebody had to remember to edit. `cost_estimate` had
# already been through this exact failure and been fixed the same way (FIX-4, B4,
# NEW-1: "the figures DERIVE from llm.SEATS so this prose can never drift from
# the seat table again"); the roster sentences were simply missed at the time.
# Anything below that names a model reads it off `llm.SEATS` at call time.
# ---------------------------------------------------------------------------

def _seat_map_phrase() -> str:
    """The whole live roster, grouped by model. Cheap derivation of the sentence
    that kept going stale."""
    by_model: Dict[str, List[str]] = {}
    for name, cfg in llm.SEATS.items():
        by_model.setdefault(cfg.model, []).append(name)
    return "; ".join(f"{model} — {'/'.join(sorted(seats))}"
                     for model, seats in sorted(by_model.items()))


def _models_for(names: List[str], env: Dict[str, str]) -> str:
    """The distinct models the NAMED seats resolve to under this env."""
    return ", ".join(sorted({llm.resolve_seat(n, env).model for n in names}))


def _probe_model() -> str:
    """The model the OPTIONAL live auth probe below would name: the cheapest
    subscription-lane seat's, by output rate. The probe sends a 1-token 'ok', so
    any live model proves login — deriving it means the recommended command can
    never name a model the product has retired (it said claude-haiku-4-5 until
    NL-147, after the no-Haiku law had removed every Haiku seat)."""
    subs = [c for c in llm.SEATS.values() if c.lane == "subscription"]
    if not subs:                              # all-api seat map: any live model
        return sorted(c.model for c in llm.SEATS.values())[0]
    return min(subs, key=lambda c: (c.usd_per_mtok_out, c.model)).model


def check_anthropic_key(env: Dict[str, str]) -> List[Result]:
    """The Claude API lane credential (B2). Required precisely when — under the
    current seat map + lane env — some seat resolves to the anthropic provider
    ON THE API LANE. When a key is present, a harmless read-only GET /v1/models
    validates it. The value is never echoed anywhere.

    NL-155 (2026-08-14) — THE LANE HALF WAS MISSING, AND IT MADE THE DOCTOR LIE.
    This filtered seats by PROVIDER alone and never looked at the lane, so on a
    keyless DEFAULT install — the shape every Claude seat has had since B3
    (2026-07-16) — it emitted a FAIL reading "the analyst, editor,
    follow_altitude, rank, script, state, writer seat(s) now run on the Claude
    API lane and cannot run without it". Both clauses were false: those seats
    run on the `claude -p` SUBSCRIPTION lane, and they run there perfectly well
    with no key at all. The doctor's whole job is to tell the principal what is
    actually wrong with his machine; a red line on a healthy install is worse
    than no line, because it trains him to discount the section.

    The three worlds now separate:
      * some anthropic seat is genuinely on the API lane -> FAIL, unchanged
        wording (it was always the right sentence, just for the wrong world);
      * no API-lane seat but NEWSLENS_LANE_FALLBACK=api is armed -> WARN: the
        default path is fine, but the fall-over he armed has nothing to fall to,
        so a CLI outage would die at the API call instead of surviving it;
      * no API-lane seat, no armed fall-over -> INFO: not needed, and say what
        WOULD make it needed.

    The models are DERIVED (see `_models_for`) — this docstring used to assert
    "rank/editor/script run Haiku 4.5 by default" and outlived that by two seat
    batches."""
    resolved = {name: llm.resolve_seat(name, env) for name in llm.SEATS}
    anthropic_seats = sorted(n for n, c in resolved.items()
                             if c.provider == "anthropic")
    # THE SEATS THAT ACTUALLY SPEND THIS KEY: anthropic provider AND api lane.
    api_seats = sorted(n for n in anthropic_seats if resolved[n].lane == "api")
    # FIX LOOP 1 (QA F-3, 2026-08-14) — A PARTITION, NOT A SUBTRACTION.
    # `sub_seats` used to be "anthropic seats minus api seats", which is only a
    # subscription bucket if every remaining lane is the subscription lane. A
    # lane comes from an env var, so `NEWSLENS_LANE=sbscription` — one dropped
    # letter — swept all seven Claude seats into it and this check printed INFO
    # "the … seat(s) run on the claude -p subscription lane": a POSITIVE claim
    # about a machine on which `check_llm_lanes` (below, same report) FAILs
    # every seat. Two sections of one doctor run contradicting each other, and
    # the reassuring one read first. The valid set is DERIVED from the dispatch
    # registry so a lane added later is recognised here without an edit.
    valid_lanes = set(llm.registered_lanes("anthropic"))
    unregistered = sorted(n for n in anthropic_seats
                          if resolved[n].lane not in valid_lanes)
    sub_seats = sorted(n for n in anthropic_seats
                       if n not in api_seats and n not in unregistered)
    armed = llm.fallback_armed(env)
    key = (env.get("ANTHROPIC_API_KEY") or "").strip()
    if not anthropic_seats:
        # Everything routes to openai (e.g. lanes overridden) — the key is not
        # needed. Say so; note it only if it happens to be set.
        if key:
            return [Result(INFO, "ANTHROPIC_API_KEY set but no seat currently "
                                 "routes to the Claude API lane — unused")]
        return [Result(INFO, "ANTHROPIC_API_KEY not needed — no seat routes to "
                             "the Claude API lane under the current seat map")]
    if unregistered:
        # The fourth world (QA F-3). This check answers "do I need this key?",
        # and under a lane nothing implements the honest answer is that the
        # question does not yet have one — so it is WITHHELD rather than
        # guessed. Not a FAIL: `check_llm_lanes` owns the root-cause verdict and
        # already prints it once per seat; a second red here would blame the
        # credential for a config error and hand the reader a fix (buy a key)
        # that changes nothing. A WARN cannot be misread as "healthy", which is
        # the whole defect being closed. Deliberately BEFORE the key-present
        # branch: a machine that cannot run a step earns no live validation
        # call, and the seat roles that line would print are exactly the ones
        # we cannot state.
        bad = sorted({resolved[n].lane for n in unregistered})
        return [Result(
            WARN,
            "ANTHROPIC_API_KEY — verdict withheld: the "
            f"{', '.join(unregistered)} seat(s) resolve to lane "
            f"{', '.join(repr(b) for b in bad)}, which has no registered "
            "implementation, so any step on those seats dies at the lane gate "
            "before a key could matter (the LLM lane map below FAILs each one "
            "— fix that first). Unset NEWSLENS_LANE / NEWSLENS_LANE_<SEAT>, or "
            "name a lane that exists: "
            f"{', '.join(llm.registered_lanes('anthropic'))}",
        )]
    seats_txt = ", ".join(api_seats or anthropic_seats)
    if not key and not api_seats:
        # The DEFAULT install. Not a failure — the subscription lane is the
        # shipped path and it needs the logged-in CLI, not this key.
        sub_txt = ", ".join(sub_seats)
        if armed:
            return [Result(
                WARN,
                f"ANTHROPIC_API_KEY not set, but NEWSLENS_LANE_FALLBACK=api is "
                f"ARMED — the {sub_txt} seat(s) run on the claude -p "
                "subscription lane and need no key, so the default path is "
                "fine; the fall-over you armed, however, has nothing to fall "
                "to: a missing/unauthed CLI would die at the API call instead "
                "of surviving it. Add a key (console.anthropic.com/settings/"
                "keys, set a monthly cap) or unset NEWSLENS_LANE_FALLBACK",
            )]
        return [Result(
            INFO,
            f"ANTHROPIC_API_KEY not needed — the {sub_txt} seat(s) run on the "
            "claude -p subscription lane against your logged-in CLI (checked "
            "in the Subscription lane section below), not the Claude API. It "
            "becomes required only if you pin a seat to the API lane with "
            "NEWSLENS_LANE_<SEAT>=api or arm NEWSLENS_LANE_FALLBACK=api",
        )]
    if not key:
        return [Result(
            FAIL,
            f"ANTHROPIC_API_KEY not set — the {seats_txt} seat(s) now run on the "
            f"Claude API lane ({_models_for(api_seats, env)}) and cannot "
            "run without it; get one at console.anthropic.com/settings/keys, "
            "set a monthly cap, add to .env",
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
        # NL-155: say what the key actually does HERE. "powers the <seats>"
        # was printed even when every one of those seats was on the
        # subscription lane and spending nothing of it.
        if api_seats:
            role = f"powers the {seats_txt} seat(s)"
        elif armed:
            role = (f"no seat spends it on the default path — it backs the "
                    f"ARMED fall-over for the {', '.join(sub_seats)} seat(s)")
        else:
            role = ("no seat spends it today — every Claude seat is on the "
                    "claude -p subscription lane; it is here for a pinned "
                    "NEWSLENS_LANE_<SEAT>=api or an armed fall-over")
        return [Result(
            PASS,
            f"ANTHROPIC_API_KEY valid — read-only GET /v1/models OK "
            f"({count} models visible, {elapsed:.1f}s); {role}",
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

    # GENERATE_HOUR_LOCAL WOKE UP (NL-146). It was dormant from 2026-07-03 — v1
    # generation was on-demand only and nothing read this var — and the doctor
    # said so in those words. Scheduling returned, so the prose does too: this is
    # now the hour `newslens schedule plist` bakes into the launchd agent. The
    # VALIDATION is unchanged and still lives in config (the single validator —
    # BUG-1 was the doctor's drifted copy of these rules); only the sentence
    # moved, because a doctor still calling this dormant would be describing the
    # machine as it was two milestones ago.
    # WHICH LAYER ACTUALLY DECIDES IS RESOLVED FIRST, because the two sentences
    # below are about the ENV LAYER and only one of the layers is the outcome
    # (QA F-3, 2026-08-14). NL-152 added the settings line underneath these and
    # left them phrased as outcomes — so on his live machine (.env line 39 sets
    # the var, Settings holds an hour) the doctor printed BOTH "GENERATE_
    # HOUR_LOCAL = 6 … the hour a newly-rendered launchd agent will fire at" AND
    # "Settings … wins over GENERATE_HOUR_LOCAL", both PASS. Two contradictory
    # facts about one hour, on the surface whose entire job is telling him what
    # is true. The env sentences now describe the VARIABLE when they are not the
    # outcome, and describe the outcome only when they are it.
    resolved, source = config.generate_hour_resolved(env)
    env_decides = source == config.HOUR_SOURCE_ENV
    default_decides = source == config.HOUR_SOURCE_DEFAULT
    if not (env.get("GENERATE_HOUR_LOCAL") or "").strip():
        out.append(
            Result(
                INFO,
                (f"GENERATE_HOUR_LOCAL not set — fine: the default "
                 f"{config.DEFAULT_GENERATE_HOUR_LOCAL} "
                 f"({config.DEFAULT_GENERATE_HOUR_LOCAL:02d}:00 local) is the "
                 "hour `newslens schedule plist` writes into the launchd agent")
                if default_decides else
                (f"GENERATE_HOUR_LOCAL not set — fine: your Settings hour "
                 f"({resolved:02d}:00 local) is the one `newslens schedule "
                 "plist` bakes into the launchd agent, so the variable is not "
                 "needed"),
            )
        )
    else:
        try:
            hour = config.generate_hour_local(env)
            out.append(
                Result(
                    PASS,
                    (f"GENERATE_HOUR_LOCAL = {hour} ({hour:02d}:00 local) — the "
                     "hour a newly-rendered launchd agent will fire at")
                    if env_decides else
                    (f"GENERATE_HOUR_LOCAL = {hour} ({hour:02d}:00 local) in "
                     "your .env — the env layer only; your Settings hour "
                     f"({resolved:02d}:00 local) overrides it, see below"),
                )
            )
        except ValueError as exc:
            out.append(Result(FAIL, f"{exc} — fix it in .env"))

    # NL-152 — WHICH LAYER ACTUALLY DECIDES, said out loud whenever the settings
    # tab has an opinion. The env lines above describe `.env` and only `.env`;
    # since the settings value BEATS them (config.generate_hour_resolved), a
    # reader who stopped at those lines would walk away with the wrong hour. One
    # extra line, emitted only when the override is live, so the common case
    # gains no noise.
    if source == config.HOUR_SOURCE_SETTINGS:
        out.append(
            Result(
                PASS,
                f"scheduled generation is set to {resolved:02d}:00 local in "
                f"Settings — that value wins over GENERATE_HOUR_LOCAL, and it "
                f"is the hour `newslens schedule plist` bakes in",
            )
        )

    return out


def check_schedule(env: Dict[str, str]) -> List[Result]:
    """NL-146 item 5 — scheduled generation, stated truthfully.

    THE HONESTY BOUND IS THE WHOLE POINT, and it is a bound on what this check
    can SEE. It can stat a file and it can read a log. It cannot see whether
    launchd has the agent loaded — that is `launchctl`'s to answer, and this
    doctor deliberately does not shell out to it: a subprocess whose absence,
    sandbox or non-zero exit would have to be interpreted is a new way to be
    confidently wrong about the one thing the reader is asking.

    So the file's presence is reported AS the file's presence, never as "the
    schedule is running", and the one command that does answer the loaded
    question is printed for his hands. Beside it goes the last recorded fire,
    because a plist that has been sitting there for a week with no fires behind
    it is exactly the shape the never-bootstrapped case takes.

    Every line here comes from `schedule.status_lines`, shared with
    `newslens schedule status`, so the doctor and the verb cannot drift into
    describing the same machine differently."""
    from . import schedule

    # SEVERITY IS PER LINE, and it rides the line's TAG (fix loop 1, QA F-7).
    # The severities below were always the intent; what was wrong was where they
    # landed. The old code computed ONE level from the whole status and stamped
    # it on `out[0]`, so on a paused-and-not-installed schedule the WARN sat on
    # "no agent file…" — a sentence that is INFO by this very table — while
    # "PAUSED by …", the sentence the warning is about, rendered INFO. Same
    # miss for the hour mismatch, which the old comment already called WARN and
    # which always rendered INFO because it is never line 0.
    #
    #   installed      -> PASS. The file is where launchd reads it.
    #   not installed  -> INFO. Scheduling is opt-in; a reader who generates by
    #                     hand is not misconfigured.
    #   paused         -> WARN. A deliberate state, but a forgotten kill switch
    #                     is silent mornings with no other symptom — which is
    #                     exactly what a doctor is for.
    #   hour mismatch  -> WARN. The agent wins over .env and nothing else says so.
    #   hour invalid   -> INFO here, because `check_optional_and_guards` already
    #                     owns the FAIL for that variable (doctor.py:595); two
    #                     checks shouting the same fact is how a doctor teaches
    #                     people to skim.
    _LEVELS = {
        schedule.LINE_INSTALLED: PASS,
        schedule.LINE_NOT_INSTALLED: INFO,
        schedule.LINE_PAUSED: WARN,
        schedule.LINE_MISMATCH: WARN,
        schedule.LINE_HOUR_ERROR: INFO,
    }
    try:
        # ONE read of the world: status_lines_tagged is handed the same dict the
        # severities are derived from, so a kill switch flipped mid-check cannot
        # produce sentences and a severity taken from two different worlds.
        tagged = schedule.status_lines_tagged(env=env)
    except Exception as exc:  # noqa: BLE001 — a doctor never dies of a check
        return [Result(WARN, f"could not read the schedule status ({type(exc).__name__}: "
                             f"{exc}) — `newslens schedule status` will say more")]

    return [Result(_LEVELS.get(tag, INFO), line) for tag, line in tagged]


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


def _run_auth_probe(bin_path: str, model: str) -> subprocess.CompletedProcess:
    """THE LIVE HALF of the opt-in auth probe (NL-160) — the only place in the
    doctor that starts a real model call.

    A NAMED module-level seam rather than an inline `subprocess.run`, and the name
    is the point: this is the single door a live `claude -p` can leave this
    process through, so it is the single door a test patches to exercise the
    verdict logic without spawning, and the single door a reader must audit to
    believe "the default doctor never spends".

    THE CHILD IS THE LANE'S OWN CHILD. Same base flags, same env allowlist, built
    from `llm`'s constants rather than retyped here — so the probe proves what the
    pipeline will actually do, and inherits the lane's guarantees rather than
    approximating them. The one that matters most: `_subscription_env` strips
    ANTHROPIC_API_KEY, so a machine that happens to carry an API key cannot turn
    this $0 subscription auth check into a metered API call. Retyping the flags
    here would be a second copy of a money-shaped rule — the exact defect class
    `llm.is_auth_failure_detail` (and the single walker it reads) exists as one
    implementation to avoid.

    Cost: the prompt is the word "ok". A rejection spends nothing at all (there is
    no session to bill); a success spends a single token of subscription quota,
    which is why the probe stays opt-in."""
    scratch = tempfile.mkdtemp(prefix="newslens-doctor-probe-")
    try:
        return subprocess.run(
            [bin_path, *llm._SUBSCRIPTION_BASE_FLAGS, "--model", model],
            input="ok",
            cwd=scratch,
            # os.environ, not the doctor's `env` mapping: the CLI finds its own
            # subscription auth through HOME, which lives in the process env and
            # not in .env. The allowlist is what makes that safe.
            env=llm._subscription_env(dict(os.environ)),
            capture_output=True, text=True, timeout=CLAUDE_PROBE_TIMEOUT_S,
        )
    finally:
        import shutil
        shutil.rmtree(scratch, ignore_errors=True)


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
    SPENDS the principal's subscription quota, so it is opt-in only
    (NEWSLENS_DOCTOR_SUBSCRIPTION_PROBE=1), never auto-fired. NL-160 built that
    probe — it was designed-and-printed until then, which is why this whole
    section ran GREEN through the 2026-08-24 outage: binary and version both
    pass on a machine whose CLI is logged out."""
    sub_seats = sorted(
        name for name in llm.SEATS
        if llm.resolve_seat(name, env).lane == "subscription"
    )
    if not sub_seats:
        # FIX LOOP 1 (2026-08-14) — QA F-3's defect class at its sibling site,
        # found while fixing the named one. The bucket being empty has TWO
        # causes and this line asserted one of them: "the api lane covers the
        # anthropic seats" is true when the seats moved to the api lane, and
        # false when they resolved to a lane nothing implements, where they are
        # covered by nothing at all. The seat test here is a positive membership
        # check (lane == "subscription"), so it never had the subtraction bug —
        # only the reassurance attached to its empty case.
        stray = sorted({c.lane for c in
                        (llm.resolve_seat(n, env) for n in llm.SEATS)
                        if c.provider == "anthropic"
                        and c.lane not in set(llm.registered_lanes("anthropic"))})
        if stray:
            return [Result(INFO, "no seat resolves to the claude -p "
                                 "subscription lane — and NOT because they "
                                 "moved to the api lane: "
                                 f"{', '.join(repr(s) for s in stray)} is not a "
                                 "registered lane, so those seats are covered "
                                 "by nothing (the LLM lane map below FAILs each "
                                 "one). Nothing to check here until that is "
                                 "fixed")]
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
    # The OPTIONAL live auth probe — BUILT AND FIRED since NL-160 (principal
    # ruling 2026-08-24, item 2). It stays opt-in for the same reason it always
    # was (a success spends a token of subscription quota), but "designed, not
    # fired" was itself the 2026-08-24 defect: the binary+version checks above
    # pass without auth, so the doctor ran GREEN through an outage that killed
    # five straight generates. A health check that cannot see the thing that
    # broke is not a health check.
    if (env.get("NEWSLENS_DOCTOR_SUBSCRIPTION_PROBE") or "").strip() == "1":
        model = _probe_model()
        try:
            proc = _run_auth_probe(bin_path, model)
        except Exception as exc:  # noqa: BLE001 — timeout / OSError, both visible
            out.append(Result(
                FAIL,
                f"the live auth probe could not run ({type(exc).__name__}: "
                f"{exc}) — the CLI at {bin_path} did not answer a 1-token "
                f"`claude -p` within {CLAUDE_PROBE_TIMEOUT_S}s; the "
                "subscription seats would hit the same wall",
            ))
        else:
            # NL-160 gate R-5: the doctor's verdict rides the SAME source
            # restriction as the transport's raise. A probe whose only output
            # was unparseable stdout still gets that text PRINTED, but it may
            # not be read as an auth rejection — one rule, two readers,
            # including the half of the rule that says which text counts.
            detail, detail_source = llm.subscription_failure_parts(
                proc.stdout, proc.stderr)
            try:
                payload = json.loads(proc.stdout)
            except (ValueError, TypeError):
                payload = None
            authed = (proc.returncode == 0 and isinstance(payload, dict)
                      and not payload.get("is_error"))
            if authed:
                out.append(Result(
                    PASS,
                    f"live auth probe: `claude -p` answered on {model} — the "
                    "CLI is LOGGED IN and the subscription seats can run "
                    "(cost: one token of quota)",
                ))
            elif llm.is_auth_failure_detail(detail, detail_source):
                # THE 2026-08-24 CLASS, now nameable by the doctor. Same
                # predicate the transport raises on — one rule, two readers.
                out.append(Result(
                    FAIL,
                    f"live auth probe: the CLI is NOT authenticated — {detail}. "
                    f"{llm.AUTH_FIX_HINT}",
                ))
            else:
                # Honest third verdict. The probe failed for something that is
                # not an auth rejection, so it proves neither login nor its
                # absence — say that rather than guess in either direction.
                out.append(Result(
                    WARN,
                    f"live auth probe was inconclusive (exit {proc.returncode}): "
                    f"{detail} — this is not an auth rejection, so login state "
                    "is still unproven",
                ))
    else:
        out.append(Result(
            INFO,
            "auth NOT probed (a live `claude -p` ping spends subscription "
            "quota) — the binary+version pass above does not prove the CLI is "
            "logged in; run `claude` once interactively to confirm login "
            "(SETUP.md). Fire the live probe with "
            "NEWSLENS_DOCTOR_SUBSCRIPTION_PROBE=1 — it costs one token when "
            "you are logged in and nothing at all when you are not",
        ))
    return out


# NL-160 (routed here by the NL-105 gate, R-4). How long a blank run has to get
# before the doctor says something. The NL-105 SUITE bound is stricter — no two
# consecutive editions may both go blank — but that pin replays a FROZEN corpus,
# and this line reads the living record, where one quiet edition is ordinary and
# a run is the signal. Three is chosen to sit far below the failure it exists to
# catch (the real drought ran TWELVE editions, 2026-07-24 to 2026-08-14, while 49
# synthetic pins stayed green) and far above ordinary noise.
ARC_DROUGHT_WARN_RUN = 3

# How many recent eligible editions the ratio is reported over.
ARC_RECENT_EDITIONS = 12


def _log_rows() -> Tuple[List[Dict], int, Optional[str], int]:
    """Every readable row of the generation record, oldest segment first.

    (rows, torn_lines, unreadable_segment_or_None, segment_count). Extracted
    from `check_arc_continuity` when the NL-163 M2 delivery probe became a
    second reader of the same record — two loops over an append-only log is
    exactly how two checks start disagreeing about what it says.

    IT READS `generate.log_segments()`, NOT the live file: a reader wired to
    the live file alone is born rotation-blind (NL-160 gate R-7/F-9), and the
    segments are disjoint by construction so nothing is counted twice.
    `errors="replace"` because a crash-interrupted line will one day end
    mid-multibyte-sequence, and a doctor that dies on it dies exactly when it
    is most needed; the replaced line then fails json.loads and is COUNTED."""
    from . import generate
    segments = [p for p in generate.log_segments() if p.exists()]
    rows: List[Dict] = []
    skipped = 0
    for segment in segments:
        try:
            with segment.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        skipped += 1
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
        except OSError as exc:
            return rows, skipped, f"{segment.name} ({exc})", len(segments)
    return rows, skipped, None, len(segments)


def _arc_editions_from_log(rows: List[Dict]) -> List[Tuple[str, bool]]:
    """(edition_date, carried_an_arc) for every ARC-ELIGIBLE edition in `rows`,
    oldest first.

    ELIGIBLE means the edition actually reached the arc author — some thread of
    its produced an arc outcome, authored or omitted. Editions with no eligible
    thread are EXCLUDED rather than counted as blank: a day whose threads did not
    move is not a day the arc machinery failed, and counting it would make the
    detector cry drought over quiet news. (That is the same population the NL-105
    streak pin speaks for — "consecutive among editions that produced arc
    candidates".)

    Keyed on memory_core's own marker constants, never on retyped prose."""
    from . import memory_core
    per: Dict[str, List[int]] = {}
    for row in rows:
        mem = row.get("memory") if isinstance(row, dict) else None
        if not isinstance(mem, dict):
            continue
        rewrites = mem.get("state_rewrites")
        date = row.get("date")
        if not isinstance(rewrites, list) or not isinstance(date, str):
            continue
        for rw in rewrites:
            detail = str(rw.get("detail") or "") if isinstance(rw, dict) else ""
            if memory_core.ARC_AUTHORED_MARK in detail:
                per.setdefault(date, [0, 0])[0] += 1
            elif memory_core.ARC_OMITTED_MARK in detail:
                per.setdefault(date, [0, 0])[1] += 1
    return [(date, per[date][0] > 0) for date in sorted(per)]


def check_arc_continuity() -> List[Result]:
    """THE LIVE ARC-DROUGHT DETECTOR (NL-160, from the NL-105 gate's R-4).

    Why it lives in the doctor and not the suite. The NL-105 streak pin is real,
    but its corpus is FROZEN: it proves the validator would not re-create the
    drought that already happened, and it cannot move for a drought that starts
    tomorrow. A detector that reads the living record cannot sit in the hermetic
    suite either (the v7-M1 sandbox law — the suite never touches real data). So
    the honest place for it is here, where reading real state is the whole job.

    $0 and offline by construction: JSON-lines file reads, no DB, no network,
    no model call. It reports what the record says and never repairs it.

    IT READS `generate.log_segments()`, NOT the live file (NL-160 gate R-7/F-9).
    That function is the house's documented reading order — "everything that
    wants the whole history" — and it is documented in the very module this
    detector reads. A reader wired to `log_file()` alone is born ROTATION-BLIND:
    after the first NL-154 cut, a drought that straddles the boundary is counted
    only from the cut forward, and a drought-detector that shortens droughts is
    the failure it exists to catch, wearing the monitor's own uniform. The
    segments are disjoint by construction, so concatenating them counts nothing
    twice (see generate.log_segments).

    `errors="replace"` (gate R-2, the diagnose._entries / NL-149 F-1
    precedent): generation_log details are full of em-dashes and §, and an
    append-only log a crash interrupts will one day end mid-multibyte-sequence.
    Decoding strictly turns that tail into a UnicodeDecodeError that only OSError
    would have to catch — and since run_doctor builds EVERY section before it
    prints anything, the whole doctor would die with zero output at precisely the
    moment a crashed generate is why it is being run. Replacing costs the torn
    line, which then dies at json.loads and is COUNTED in `skipped`."""
    from . import generate
    live = generate.log_file()
    rows, skipped, unreadable, segment_count = _log_rows()
    if not segment_count:
        return [Result(INFO, "no generation log yet — arc continuity has "
                             "nothing to read (this is a fresh install)")]
    if unreadable:
        return [Result(WARN, f"could not read {unreadable} — arc "
                             "continuity unknown")]

    editions = _arc_editions_from_log(rows)
    if not editions:
        return [Result(INFO, "no arc-eligible edition on record yet — the arc "
                             "line needs a thread with a prior covered edition")]

    recent = editions[-ARC_RECENT_EDITIONS:]
    carried = sum(1 for _, ok in recent if ok)
    blank_run: List[str] = []
    for date, ok in reversed(editions):
        if ok:
            break
        blank_run.append(date)
    blank_run.reverse()

    out: List[Result] = []
    headline = (f"{carried} of the last {len(recent)} arc-eligible editions "
                f"carried a continuity line")
    if len(blank_run) >= ARC_DROUGHT_WARN_RUN:
        out.append(Result(
            WARN,
            f"arc DROUGHT: {len(blank_run)} consecutive eligible editions have "
            f"served no continuity line ({blank_run[0]} to {blank_run[-1]}) — "
            f"{headline}. The deep views are running without the memory moat's "
            "reader-visible line; check the arc rejections in "
            f"{live.name} (state_rewrites[].detail) before it goes another week",
        ))
    elif blank_run:
        out.append(Result(
            PASS,
            f"{headline} — the most recent {len(blank_run)} eligible "
            f"edition(s) served none, below the {ARC_DROUGHT_WARN_RUN}-edition "
            "drought line",
        ))
    else:
        out.append(Result(
            PASS, f"{headline} — the most recent eligible edition served one"))
    if skipped:
        out.append(Result(WARN, f"{skipped} unparseable line(s) in the "
                                f"{live.name} record were skipped — the arc "
                                "counts above are over the readable rows only"))
    return out


# NL-163 M2. How far back the artifact census looks. Long enough that a hole
# is visible for more than a day, short enough that a fixed old gap does not
# nag forever.
BUNDLE_CENSUS_EDITIONS = 14


def _published_dates(rows: List[Dict]) -> List[str]:
    """Edition dates that were actually PUBLISHED, oldest first.

    Samples are excluded because a sample is explicitly not the briefing of
    record and never mints (generate's mount) — counting one would make the
    census demand an artifact the code refuses to write. Failed runs are
    excluded for the same reason: nothing was published to freeze."""
    dates = []
    for row in rows:
        if row.get("sample"):
            continue
        if row.get("status") not in (None, "ok"):
            continue
        date = row.get("date")
        if isinstance(date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            dates.append(date)
    return sorted(set(dates))


def check_phone_delivery(env: Dict[str, str]) -> List[Result]:
    """NL-163 Stage-A: does the phone actually have the paper?

    TWO INSTRUMENTS, both stat-only (M1 gate rider R-A, and the doctor's own
    honesty bound — it reads files, it does not probe other machines):

      1. ARTIFACT PRESENCE vs THE LOG. A mint failure is contained at the
         publish seam and warns in the run's output, which is gone by the next
         morning; the log entry is already serialised when that warning is
         written, so the record does NOT carry it. The cold-forensics
         comparison the mount's own comment names — `data/briefings/*.phone.json`
         against the log's dates — is therefore the only durable detector, and
         this makes it a standing one.
      2. PUSH AGE. The last delivery this machine recorded, against the newest
         frozen edition it holds. NEVER A LIVENESS CLAIM: no request is made
         from here, so this says "nothing has been delivered since X", never
         "the host is up" or "the host still has it".
    """
    from . import editionbundle, pushclient

    out: List[Result] = []

    # -- 1. the census -----------------------------------------------------
    bundles = editionbundle.available_dates()
    rows, skipped, unreadable, segment_count = _log_rows()
    published = _published_dates(rows)
    if unreadable:
        out.append(Result(WARN, f"could not read {unreadable} — the frozen-"
                                "edition census is unknown for this run"))
    elif not segment_count or not published:
        out.append(Result(INFO, "no published editions on record yet — nothing "
                                "to freeze for the phone"))
    elif not bundles:
        out.append(Result(
            INFO,
            f"no frozen phone editions on disk yet ({len(published)} published "
            "editions on record) — the next generate mints the first. Editions "
            "published before the bundle existed are NOT reconstructed: a "
            "rebuild would render today's follow state and stamp it "
            "frozen-at-publish"))
    else:
        # Only the era in which artifacts exist can be missing one. Everything
        # before the first bundle is the honest pre-NL-163 archive.
        era = [d for d in published if d >= bundles[0]][-BUNDLE_CENSUS_EDITIONS:]
        missing = [d for d in era if d not in set(bundles)]
        if missing:
            out.append(Result(
                WARN,
                f"{len(missing)} of the last {len(era)} published editions have "
                f"NO frozen artifact ({', '.join(missing)}) — the mint failed "
                "at those publishes and the phone will never carry those dates "
                "(they are not rebuilt) — the run that failed said so at the "
                "time, in a 'phone bundle: NOT minted' warning the record does "
                "not keep"))
        else:
            out.append(Result(
                PASS,
                f"every one of the last {len(era)} published editions has a "
                f"frozen artifact ({bundles[-1]} newest, {len(bundles)} on disk)"))
    if skipped:
        out.append(Result(WARN, f"{skipped} unparseable line(s) in the "
                                "generation record were skipped — the census "
                                "above is over the readable rows only"))

    # -- 2. the push -------------------------------------------------------
    url, token = pushclient.config(env)
    if not url and not token:
        out.append(Result(
            INFO,
            f"phone push not configured ({pushclient.URL_VAR} and "
            f"{pushclient.TOKEN_VAR} unset) — editions are frozen locally and "
            "delivered nowhere. `newslens bundle --open` reads them on this "
            "machine; the hosted paper is what the variables turn on"))
        return out
    if not url or not token:
        out.append(Result(
            FAIL,
            f"phone push is half-configured — "
            f"{pushclient.URL_VAR if not url else pushclient.TOKEN_VAR} is "
            "missing. Set both in .env or neither"))
        return out
    try:
        pushclient.endpoint(url, "2026-01-01")
    except pushclient.PushError as exc:
        out.append(Result(FAIL, f"{pushclient.URL_VAR} is not a stream "
                                f"endpoint — {exc}"))
        return out
    host = pushclient._host_of(url)
    out.append(Result(PASS, f"phone push configured → {host} (the token is set; "
                            "the host stores only its sha256)"))

    state = pushclient.read_state()
    success, attempt = state.get("last_success"), state.get("last_attempt")
    newest = bundles[-1] if bundles else None
    if not isinstance(success, dict):
        out.append(Result(
            WARN,
            "no successful push has been recorded on this machine" +
            (f" — {newest} is frozen and undelivered; `newslens push` sends it"
             if newest else " (nothing frozen to send yet)")))
    else:
        delivered, when = success.get("date"), (success.get("host_pushed_at")
                                                or success.get("at") or "—")
        if newest and delivered != newest:
            out.append(Result(
                WARN,
                f"the newest frozen edition ({newest}) has NOT been delivered — "
                f"the last recorded delivery was {delivered} at {when}. "
                f"`newslens push` re-sends the artifact"))
        else:
            out.append(Result(
                PASS,
                f"last delivery recorded: {delivered} (host receipt {when}) — "
                "this is a local record of a push this machine made, not a "
                "probe: whether the host still holds it is not visible from here"))
    if isinstance(attempt, dict) and attempt.get("status") == "failed":
        # A failure AFTER the last success is the shape that matters: it means
        # the most recent thing that happened was a paper that did not land.
        if not isinstance(success, dict) or str(attempt.get("at") or "") > str(
                success.get("at") or ""):
            out.append(Result(
                WARN,
                f"the last push attempt FAILED ({attempt.get('date')}, "
                f"{attempt.get('http_status') or 'no answer'}): "
                f"{str(attempt.get('detail') or '')[:160]}"))
    return out


def check_phone_auth(env: Dict[str, str]) -> List[Result]:
    """NL-163 M3 — is the paper's door configured, and on which side?

    THE SPLIT THIS SECTION EXISTS TO MAKE VISIBLE (the principal's ruling
    2026-08-31): the operator's vendor SECRET lives here, on this machine, and
    is used by exactly one command; the paper's HOST holds only a public token
    and a key URL. Two machines, two halves — and a section that confused them
    would be the fastest route to a secret on a rented box.

    NAMES ONLY. No value out of `.env` is ever printed: presence, shape and
    length are the whole of what this reports.

    THE LIVE PROBE IS OPT-IN (`NEWSLENS_DOCTOR_PHONE_AUTH_PROBE=1`) — the
    NL-160 subscription-probe pattern. It fetches the project's PUBLIC KEY SET
    and nothing else: read-only, no user touched, no sign-in attempted, and
    structurally incapable of a metered call (there is no endpoint reachable
    from here that bills a monthly active user). Off by default because a
    health check that phones a third party on every run is a health check that
    fails on their bad afternoon.
    """
    from . import phoneaccount

    out: List[Result] = []
    project_id = (env.get(phoneaccount.PROJECT_ID_VAR) or "").strip()
    secret = (env.get(phoneaccount.SECRET_VAR) or "").strip()

    if not project_id and not secret:
        return [Result(
            INFO,
            "phone sign-in is not configured on this machine yet — "
            f"{phoneaccount.PROJECT_ID_VAR} and {phoneaccount.SECRET_VAR} are "
            "unset. They are the OPERATOR's half (creating readers' accounts); "
            "the paper's host never holds them. SETUP.md, 'The phone door'")]

    where = None
    if not project_id:
        out.append(Result(
            FAIL,
            f"{phoneaccount.SECRET_VAR} is set but {phoneaccount.PROJECT_ID_VAR} "
            "is not — a secret alone cannot say which project it belongs to, so "
            "`newslens phone-account` refuses. Copy the project id from the "
            "vendor dashboard's API Keys page"))
    else:
        try:
            where = phoneaccount.environment_for(project_id)
            out.append(Result(
                PASS,
                f"{phoneaccount.PROJECT_ID_VAR} present and well-formed "
                f"({where} project) — `newslens phone-account create` would "
                f"call {phoneaccount.API_HOSTS[where]}"))
        except phoneaccount.AccountError as exc:
            out.append(Result(FAIL, f"{phoneaccount.PROJECT_ID_VAR}: {exc}"))

    if not secret:
        out.append(Result(
            WARN,
            f"{phoneaccount.SECRET_VAR} is not set — accounts cannot be created "
            "from here. `newslens phone-account create` still runs as a dry run "
            "and prints the call it would make"))
    else:
        out.append(Result(
            PASS,
            f"{phoneaccount.SECRET_VAR} present ({len(secret)} characters) — "
            "never printed, never logged, and never sent to the paper's host"))

    if where == "test":
        out.append(Result(
            WARN,
            "this is the vendor's TEST project — accounts created here do not "
            "exist for a live paper. Flip to the live project before the "
            "acceptance morning; SETUP.md step 1 is the kill-check that settles "
            "whether passkeys are available there at all"))

    # The runbook's own drift check: the host-side names are documented, or the
    # deploy step is a guess.
    try:
        documented = (paths.PROJECT_ROOT / ".env.example").read_text(
            encoding="utf-8")
    except OSError:
        documented = ""
    undocumented = [name for name in
                    ("NEWSLENS_STYTCH_PROJECT_ID", "NEWSLENS_STYTCH_PUBLIC_TOKEN",
                     "NEWSLENS_STYTCH_JWKS_URL",
                     # Added M4: SETUP step 5 instructs setting this, and it is
                     # the repair path for the two SDK methods the shipped
                     # default artifact is missing. A runbook step naming a
                     # variable no reference documents is exactly the drift
                     # this check exists to catch — it just wasn't watching
                     # this name yet.
                     "NEWSLENS_STYTCH_SDK_URL", "NEWSLENS_USER_STREAMS")
                    if name not in documented]
    if undocumented:
        out.append(Result(
            WARN,
            f"the host-side names {', '.join(undocumented)} are missing from "
            ".env.example — the deploy runbook would be a guess"))

    if str(env.get("NEWSLENS_DOCTOR_PHONE_AUTH_PROBE", "")).strip().lower() not in (
            "1", "true", "yes"):
        out.append(Result(
            INFO,
            "live key-set probe not run (set NEWSLENS_DOCTOR_PHONE_AUTH_PROBE=1 "
            "to fetch this project's public keys — read-only, $0, no user "
            "touched, no metered call possible)"))
        return out

    if not (project_id and secret and where):
        out.append(Result(WARN, "live key-set probe skipped — the credentials "
                                "above are incomplete"))
        return out
    try:
        result = phoneaccount.jwks(env)
    except phoneaccount.AccountError as exc:
        out.append(Result(
            FAIL,
            f"live key-set probe FAILED: {exc}. Without a readable key set the "
            "paper's host cannot verify one session — pin the keys into the "
            "host's NEWSLENS_STYTCH_JWKS instead (`newslens phone-account "
            "jwks`)"))
        return out
    out.append(Result(
        PASS,
        f"live key-set probe OK: {len(result['kids'])} signing key(s) at "
        f"{result['url']} — the host verifies sessions against these and "
        "reaches no other vendor endpoint"))
    return out


def check_llm_lanes(env: Dict[str, str]) -> List[Result]:
    """The provider-seam lane map: one line per seat showing the resolved
    provider/model/lane + per-seat price, plus fallback state. Standing shape
    (item C, 2026-07-17): the anthropic seats DEFAULT to the SUBSCRIPTION lane
    with the api lane as each one's registered fall-over. A seat resolved (via
    NEWSLENS_LANE / NEWSLENS_LANE_<SEAT>) to a lane with no registered provider
    is flagged FAIL here — the same fail-loud condition the run itself hits.

    The per-seat lines below have ALWAYS been derived from `llm.SEATS`; it was
    the summary sentence that went stale (it named rank/editor/script/state as
    Haiku seats through two seat batches), so the summary is derived now too —
    see `_seat_map_phrase`."""
    out: List[Result] = []
    for name in llm.SEATS:
        cfg = llm.resolve_seat(name, env)
        line = (f"{name}: {cfg.provider}/{cfg.model} · lane={cfg.lane} · "
                f"timeout {cfg.timeout_s}s · "
                f"${cfg.usd_per_mtok_in:.2f}/${cfg.usd_per_mtok_out:.2f} per MTok")
        try:
            # NL-156: preflight only (no live call) — and against THIS env, the
            # same one `resolve_seat` just used. Until 2026-08-14 the binary leg
            # read os.environ, so a NEWSLENS_CLAUDE_BIN set in .env but not
            # exported was honoured by the "Subscription lane" section below
            # (which passes env) and ignored by this one.
            llm.check_lane(cfg, env)
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
        f"(the claude -p lane, B3) — the seat map is [{_seat_map_phrase()}], and "
        "the anthropic seats DEFAULT to subscription with api as their fall-over "
        "(item C, 2026-07-17 — field-proven edition 7)",
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
    sections.append(("Scheduled generation", check_schedule(env)))   # NL-146
    sections.append(("Arc continuity", check_arc_continuity()))      # NL-160
    sections.append(("Phone edition & delivery", check_phone_delivery(env)))  # NL-163
    sections.append(("Phone sign-in", check_phone_auth(env)))                 # NL-163 M3
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
