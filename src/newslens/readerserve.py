"""The reader-serve door — serve ANY profile, $0 by default, one command.

NL-132-B item 1 (charter: the NL-132 engineering-2 scoping, option B). The
mechanisms this wraps all shipped already; what did not exist was a door that
makes the safe form the DEFAULT one.

WHAT THE WRAPPER IS FOR. The manual recipe works today:

    PERPLEXITY_API_KEY= newslens --profile fresh1 serve --port 8490

and every part of it is load-bearing. `PERPLEXITY_API_KEY=` sets the variable
PRESENT AND EMPTY, which `config.load_env()`'s `load_dotenv(override=False)`
leaves alone, so every consumer reads it as absent and `_sonar_verify` takes
its honest skip path. The obvious spelling — `env -u PERPLEXITY_API_KEY` —
makes the variable ABSENT, at which point load_env re-injects the principal's
live key from `.env` and the served page's one-click "Generate today's
edition" button spends metered Sonar money. (personas.zero_dollar_env carries
the full derivation; that docstring is the sanctioned semantics, this module
is the default.)

That is a distinction humans get wrong under repetition, and the failure mode
is silent money. Rook's law from the scoping: at review cadence the scrub must
be structural, not a shell prefix somebody has to remember. So:

  * the $0 scrub is applied to THIS process, then MEASURED in a child after
    load_env, through `personas.enforce_zero_dollar` — the ONE implementation
    both persona doors already use. A door that cannot prove the scrub took
    serves nothing;
  * `--live` is the only way past it, it is explicit, and it says out loud
    that the session can spend (the key's LENGTH, never the key);
  * `--create <slug>` mints the world and serves it in the same command;
  * the port is PROBED, never assumed — never the founder's 8484, never one
    something is already holding;
  * the profile's own ledger is printed at exit, so a review walk's spend is
    disclosed per walk instead of assumed.

The founder's own world is NOT servable here: his instance is `newslens serve`
on 8484, and a second server that migrates his database on startup is not a
review sandbox. Same shape as the persona door's "the founder is never a
persona".

Thin by construction: like the persona doors, the serve itself DELEGATES to
`cli.main`, so the incident guard, the profile boundary, the redirection
disclosure and the verb are all the shipped code and this module adds no
second copy of any of them.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import paths, personas

LEDGER_NAME = "generation_log.jsonl"
LOOPBACK = "127.0.0.1"
# Deliberately above the persona block (8485-8487) and clear of the founder's
# 8484, so a reader world never competes for a port that has an owner. Every
# candidate in the span is still probed — a range is not evidence.
PORT_SPAN = (8490, 8599)
RESERVED_PORTS = (personas.FOUNDER_PORT,)
# Seconds the liveness half of `port_is_free` will wait for an answer. See that
# function: without a bound, a port that is bound-but-not-listening costs 25.9
# real seconds, times however many of them are in the span.
PROBE_TIMEOUT = 0.25


class ReaderServeError(Exception):
    """A usage or environment problem this door refuses to guess past."""


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------

@dataclass
class Options:
    slug: str
    create: bool
    live: bool
    port: Optional[int]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scripts/reader-serve",
        description="Serve one reader's profile at $0, on a probed free port.",
        epilog="The founder's own world is served by `newslens serve` (8484) "
               "and is refused here.")
    p.add_argument("slug", nargs="?", default=None,
                   help="the profile to serve")
    p.add_argument("--create", nargs="?", const="", default=None, metavar="SLUG",
                   help="provision the profile first, then serve it. Takes the "
                        "slug, or stands alone beside the positional one")
    p.add_argument("--live", action="store_true",
                   help="OPT IN to the real PERPLEXITY_API_KEY — this session "
                        "can spend metered Sonar money. Off by default")
    p.add_argument("--port", type=int, default=None, metavar="N",
                   help=f"serve on exactly this port (still probed; "
                        f"{personas.FOUNDER_PORT} is refused). Default: the "
                        f"first free port in {PORT_SPAN[0]}-{PORT_SPAN[1]}")
    return p


def parse_args(argv: List[str]) -> Options:
    """Raises ReaderServeError on a usage problem, SystemExit on argparse's own
    `-h` / parse errors (main converts that to an exit code)."""
    args = build_parser().parse_args(argv)
    create = args.create is not None
    named = args.create or args.slug
    if create and args.create and args.slug and args.create != args.slug:
        raise ReaderServeError(
            f"two different profiles named: `{args.slug}` and "
            f"`--create {args.create}`. Say it once.")
    if not named:
        raise ReaderServeError(
            "which reader? usage: scripts/reader-serve <slug> [--live] "
            "[--port N]  |  scripts/reader-serve --create <slug>")
    return Options(slug=named, create=create, live=bool(args.live),
                   port=args.port)


# ---------------------------------------------------------------------------
# The port — probed, not assumed
# ---------------------------------------------------------------------------

def port_is_free(port: int, host: str = LOOPBACK) -> bool:
    """Two questions, because they have different wrong answers.

    `connect_ex` catches a live listener (the persona door's check). A bind
    attempt additionally catches a socket that is bound but not listening, a
    port in TIME_WAIT, and a privileged/reserved port. The bind probe
    deliberately does NOT set SO_REUSEADDR while `ThreadingHTTPServer` does, so
    this check is strictly STRICTER than the server it is choosing for — the
    safe direction to be wrong in.

    MEASURED HONESTY about the two arms (NL-132-B fix loop 1): for the FREE /
    NOT-FREE answer the bind arm SUBSUMES the connect arm on this platform — a
    plain bind against a live listener is refused EADDRINUSE even though the
    listener set SO_REUSEADDR (measured, errno 48). So the connect arm is not a
    second line of defence, and a mutation that removes it cannot change this
    function's return value. What it is good for is telling a LIVE listener
    from a merely-taken port, which `pick_port` now says out loud — that is
    where it becomes observable, and where its pin lives.
    """
    return not probe_port(port, host)[1]


def port_has_listener(port: int, host: str = LOOPBACK) -> bool:
    """Is something actually SERVING on this port right now?

    The connect arm on its own, for a caller that wants only that answer. A
    port can be unavailable with nothing serving on it (bound-not-listening,
    TIME_WAIT, reserved), and telling someone to go stop a serve that does not
    exist is a worse answer than no answer."""
    return probe_port(port, host)[0]


def probe_port(port: int, host: str = LOOPBACK) -> Tuple[bool, bool]:
    """(a listener answered, the port is taken) — one place, both questions.

    ONE CALL PER DECISION, and that is load-bearing rather than tidy: the
    connect arm leaves a completed connection in the listener's accept queue
    that nothing ever accepts, so asking twice against a `listen(1)` backlog
    finds it full and the SECOND probe times out and reports NO listener. That
    is exactly how this function came to exist — `pick_port` asked the two
    questions with two probes and mislabelled a live serve as merely-bound
    (caught by its own new pin, in the same loop it was written).

    PROBE_TIMEOUT is not decoration (NL-132-B fix loop 1, found by staging the
    arm QA F-4 showed was never staged): an unlimited `connect_ex` against a
    port that is BOUND BUT NOT LISTENING does not come back "refused" — on this
    machine it sat in SYN retransmit for **25.9 SECONDS** and returned
    ETIMEDOUT. `pick_port` walks a 110-port span, so a single such port turned
    the auto-port door into a half-minute hang and a spanful into a wait nobody
    would sit through. A live listener on loopback answers in well under a
    millisecond, so a quarter of a second is three orders of magnitude of
    headroom.

    Inherently a moment's answer: something can take the port between this
    probe and the bind a second later. That race is disclosed rather than
    designed away — the serve prints the port it got, and a collision surfaces
    as the server's own error, not as a silent second instance.
    """
    listener = False
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(PROBE_TIMEOUT)
    try:
        if probe.connect_ex((host, port)) == 0:
            listener = True
    except OSError:                 # timed out or unreachable: not a listener
        pass
    finally:
        probe.close()
    if listener:
        return True, True
    binder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        binder.bind((host, port))
    except OSError:
        return False, True          # taken, but nothing is serving on it
    finally:
        binder.close()
    return False, False


def pick_port(preferred: Optional[int] = None,
              span: Tuple[int, int] = PORT_SPAN,
              reserved: Tuple[int, ...] = RESERVED_PORTS) -> int:
    if preferred is not None:
        if preferred in reserved:
            raise ReaderServeError(
                f"refusing port {preferred} — that is the founder's own "
                "instance. Leave it to `newslens serve`.")
        # The two arms of the probe answer different questions, so the refusal
        # says which one refused — from ONE probe (see probe_port: asking
        # twice mislabels a live serve). Before NL-132-B fix loop 1 both cases
        # printed "something is serving there", which is simply untrue of a
        # port that is bound-but-not-listening, in TIME_WAIT, or reserved, and
        # sends the operator hunting for a serve that does not exist. It also
        # left the connect arm with no observable consequence at all, i.e.
        # unpinnable — QA F-2's lesson applied to QA F-4's finding.
        listening, taken = probe_port(preferred)
        if taken:
            if listening:
                raise ReaderServeError(
                    f"port {preferred} is already in use — something is "
                    "serving there. Omit --port and one will be picked.")
            raise ReaderServeError(
                f"port {preferred} is not available — it is taken but nothing "
                "is serving on it (a socket bound without listening, a "
                "TIME_WAIT from a serve that just stopped, or a reserved "
                "port). Omit --port and one will be picked.")
        return preferred
    for port in range(span[0], span[1] + 1):
        if port in reserved:
            continue
        if port_is_free(port):
            return port
    raise ReaderServeError(
        f"no free port between {span[0]} and {span[1]} — stop a stale serve "
        "(the 2026-07-31 lesson: standing serves outlive their walk) or pass "
        "--port explicitly.")


# ---------------------------------------------------------------------------
# The ledger — this profile's own, never the founder's
# ---------------------------------------------------------------------------

@dataclass
class Ledger:
    path: Path
    exists: bool
    entries: List[Dict]
    malformed: int

    @property
    def count(self) -> int:
        return len(self.entries)


def _num(value) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def entry_money(entry: Dict) -> Tuple[float, float]:
    """(charged, shadow) for one generation_log line.

    `usd` is REAL MONEY and `usd_shadow` is the API-equivalent the budget cap
    actually guards — on the subscription lane charged is $0 and shadow is not
    (NL-95; generate.fold_late_steps carries the law).

    The analysis stage bills in its own module and is NOT a `report.steps` row:
    a successful entry carries `analysis_usd` / `analysis_usd_shadow` as
    top-level keys while `total_usd` sums only the steps, so a summary that
    ignored them would under-report exactly the metered Sonar money this door
    exists to prove is zero (verified against the founder's own log: an entry
    with total_usd 0 and analysis_usd 0.013814). A FAILED entry is the mirror
    image — `fold_late_steps` puts the analysis row INSIDE steps and the
    top-level keys are absent — so adding them here cannot double-count. The
    memory stage is likewise already the `state_rewrites` step and is not added
    again.
    """
    steps = [s for s in (entry.get("steps") or []) if isinstance(s, dict)]
    charged = entry.get("total_usd")
    if not isinstance(charged, (int, float)):
        charged = sum(_num(s.get("usd")) for s in steps)
    shadow = sum(_num(s.get("usd_shadow") if s.get("usd_shadow") is not None
                      else s.get("usd")) for s in steps)
    charged = _num(charged) + _num(entry.get("analysis_usd"))
    shadow += _num(entry.get("analysis_usd_shadow"))
    return round(charged, 6), round(shadow, 6)


def totals(entries: List[Dict]) -> Tuple[float, float]:
    charged = shadow = 0.0
    for entry in entries:
        c, s = entry_money(entry)
        charged += c
        shadow += s
    return round(charged, 6), round(shadow, 6)


def read_ledger(slug: str, anchor: Optional[Path] = None) -> Ledger:
    """Read-only. A malformed line is COUNTED, never swallowed: a spend record
    we could not parse is information, not noise (diagnose._load_entries'
    discipline)."""
    path = paths.profile_layout(slug, anchor)["DATA_DIR"] / LEDGER_NAME
    if not path.exists():
        return Ledger(path=path, exists=False, entries=[], malformed=0)
    entries, bad = [], 0
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        # Unreadable is a fact about the ledger, not a reason to crash the
        # door on its way out; malformed=-1 makes it visible in the printout.
        return Ledger(path=path, exists=True, entries=[], malformed=-1)
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
        else:
            bad += 1
    return Ledger(path=path, exists=True, entries=entries, malformed=bad)


def session_lines(before: Ledger, after: Ledger, live: bool) -> List[str]:
    """What this session charged and shadowed, from the profile's own ledger."""
    out = [
        "",
        "─" * 72,
        f"LEDGER — this profile's own: {after.path}",
    ]
    if not after.exists:
        out.append("  no generation_log yet — nothing was generated in this "
                   "world during this session.")
        return out
    if after.count >= before.count and after.entries[:before.count] == before.entries:
        new = after.entries[before.count:]
        label = f"this session appended {len(new)} entr" \
                f"{'y' if len(new) == 1 else 'ies'}"
    else:
        # The file was rewritten or truncated under us — say so instead of
        # printing a delta that would be arithmetic on two different files.
        new = after.entries
        label = (f"the ledger changed shape during this session "
                 f"(was {before.count} entries, now {after.count}) — showing "
                 f"the WHOLE file, not a delta")
    out.append(f"  {label}:")
    for entry in new[-10:]:
        charged, shadow = entry_money(entry)
        out.append(f"    {entry.get('ts', '?')}  {entry.get('status', '?'):<7}"
                   f"charged ${charged:.4f}   shadow ${shadow:.4f}")
    if len(new) > 10:
        out.append(f"    … and {len(new) - 10} earlier entr"
                   f"{'y' if len(new) - 10 == 1 else 'ies'}")
    s_charged, s_shadow = totals(new)
    l_charged, l_shadow = totals(after.entries)
    out.append(f"  session:   charged ${s_charged:.4f}  ·  shadow "
               f"${s_shadow:.4f}")
    out.append(f"  lifetime:  charged ${l_charged:.4f}  ·  shadow "
               f"${l_shadow:.4f}   ({after.count} entries)")
    if after.malformed:
        out.append(f"  {after.malformed} unparseable line(s) in this ledger — "
                   "not counted above, and worth a look")
    out.append("  charged is REAL money; shadow is the API-equivalent the "
               "budget cap guards")
    out.append("  ($0 on the subscription lane — NL-95).")
    if not live:
        out.append("  This session ran $0-scrubbed: no metered Sonar call "
                   "could fire from it.")
    return out


# ---------------------------------------------------------------------------
# The door
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    personas._ensure_runtime("reader-serve")
    from . import cli, profiles

    try:
        opts = parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:                      # argparse's -h / usage error
        return int(exc.code or 0)
    except ReaderServeError as exc:
        print(f"reader-serve: {exc}", file=sys.stderr)
        return 2

    try:
        slug = paths.normalize_profile(opts.slug)
    except paths.ProfileError as exc:
        print(f"reader-serve: {exc}", file=sys.stderr)
        return 2
    if slug == paths.DEFAULT_PROFILE:
        print("reader-serve: refusing — that is the founder's own world, not a "
              "review sandbox. His instance is `newslens serve` on port "
              f"{personas.FOUNDER_PORT}; a second server would migrate and "
              "write his database behind his back.", file=sys.stderr)
        return 2

    # This door mints and serves REAL profile worlds, so it self-sanctions the
    # incident guard exactly as `persona-provision` does. cli.main sanctions
    # again at the handoff; allow_real_paths is idempotent.
    paths.allow_real_paths()

    # THE SCRUB, FIRST — before anything in this process can resolve a key.
    # (personas.scrub_this_process is the one implementation; enforce_zero_
    # dollar below re-applies it and then MEASURES that it took.)
    if not opts.live:
        personas.scrub_this_process()

    if opts.create:
        try:
            st = profiles.create(slug)
        except (paths.ProfileError, profiles.ProfileExistsError,
                FileNotFoundError) as exc:
            print(f"reader-serve: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:                   # entrypoint boundary: loud
            print(f"reader-serve: creating {slug!r} failed: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(f"created profile {st.slug!r} — {st.line()}")
        print(f"  root: {st.root}")
        print("  nothing inherited: 0-byte memory.md, no threads, interests "
              "EMPTY (the Commissioning page is this profile's first screen)")
    elif not profiles.exists(slug):
        print(f"reader-serve: no profile named {slug!r} — mint it with "
              f"`scripts/reader-serve --create {slug}` (a typo must not "
              "silently become a new reader's world).", file=sys.stderr)
        return 2

    try:
        port = pick_port(opts.port)
    except ReaderServeError as exc:
        print(f"reader-serve: {exc}", file=sys.stderr)
        return 2

    env_file = paths.profile_layout(slug)["ENV_FILE"]
    if opts.live:
        try:
            probe = personas.pipeline_probe(dict(os.environ), env_file)
        except personas.PersonaError as exc:
            print(f"reader-serve: cannot measure this environment — {exc}",
                  file=sys.stderr)
            return 1
        n = int(probe.get("key_len", 0))
        cap, cap_error = probe.get("cap"), probe.get("cap_error")
        if n:
            # The LENGTH, never the key: a 53-character key leaked into a
            # transcript once already (QA-1), and a length is enough to prove
            # the opt-in took.
            print(f"LIVE LANE (--live): the pipeline resolves a {n}-character "
                  f"{personas.SONAR_KEY_ENV} after load_env.")
            print("  This session CAN SPEND metered Sonar money — every "
                  "Generate this page starts is billed.")
            print("  Per-run budget cap: "
                  + (f"${cap:.2f}" if isinstance(cap, (int, float))
                     else f"UNREADABLE ({cap_error})"))
        else:
            print("LIVE LANE (--live) was asked for, but the pipeline resolves "
                  f"NO {personas.SONAR_KEY_ENV} after load_env — Sonar will be "
                  "skipped and the edition will say so.")
    else:
        refusal = personas.enforce_zero_dollar(slug)
        if refusal is not None:
            print(f"reader-serve: {refusal}", file=sys.stderr)
            return 1
        print(f"{personas.SONAR_KEY_ENV} scrubbed for this process only "
              "(verified absent after load_env, in a child) — including "
              "anything this instance's UI can start.")

    personas._disclose_environment(slug)
    before = read_ledger(slug)
    print(f"reader {slug} — serving this profile's own world at "
          f"http://{LOOPBACK}:{port}/")
    print(f"  world:  {paths.profile_root(slug)}")
    print(f"  founder: untouched on {personas.FOUNDER_PORT}; this is a second "
          "instance, not a switch inside his")
    print("  Ctrl-C stops it; the world persists on disk. "
          f"Remove it with `newslens profile delete {slug} --confirm {slug}`.")
    try:
        return cli.main(["--profile", slug, "serve", "--port", str(port)])
    finally:
        for line in session_lines(before, read_ledger(slug), opts.live):
            print(line)
