"""Synthetic persona fixtures (Stage-0 M3).

Three org-authored reader worlds the org can generate briefings for and read
critically — two near neighbours that form the shallow-personalization
kill-test, and one deliberately off-distribution reader. `personas/README.md`
carries the WALL and the probe design; the fixtures in `personas/*.yaml` carry
each reader's design intent next to their tags.

**They are quality-audit fixtures and never engagement evidence.** Nobody chose
these tags; there is no preference here to discover and no n to count.

This module owns four things, and owns each of them exactly once:

  1. **The fixture format** — what a persona file may say, validated loudly.
  2. **The altitude mapping** — NL-17 catalog `domain`/`topic` onto the
     shipped `broad`/`granular` storage keys. One place, so that when the
     interests file catches up to the catalog no fixture is re-authored.
  3. **Provisioning** — `profile create` (the shipped lane) followed by the
     shipped topic editor. No second YAML writer is built here; the hand-rolled
     one in server.py is already a PREFLIGHT-caution file and a rival copy is
     how BUG-1 shipped in two places at once.
  4. **The $0 contract** — the environment a persona generate runs in, and the
     proof that it took. See `zero_dollar_env` for why deleting a variable is
     the wrong move and setting it empty is the right one.

Nothing here reads, writes or resolves the founder's world. `provision` refuses
the default profile by name (and `profiles.create` refuses it again underneath).

Stdlib-only at import time (db.py's rule): PyYAML is imported lazily inside the
one function that parses a fixture, so a pre-install doctor can still enumerate
personas by filename.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import paths

# Shipped code, not reader state: same class as migrations/, prompts/ and
# templates/. NOT guarded and NOT profile-scoped — a fixture is the committed
# half of a persona; `profiles/<slug>/` is the local, gitignored other half.
PERSONAS_DIR = paths.PROJECT_ROOT / "personas"

# The founder keeps 8484 and does not move (server.DEFAULT_PORT — pinned equal
# by test rather than imported, so this module stays free of the 4600-line
# server import at module scope).
FOUNDER_PORT = 8484

# NL-17 catalog altitude -> shipped sources.yaml storage key. THE mapping;
# every other reference in the codebase goes through this dict.
#
# The catalog vocabulary is Domain -> Topic -> Entity (DECISIONS 2026-07-25:
# the Commissioning's picker builds on the NL-17 catalog, "never on the dying
# broad/granular vocabulary"). config.py still stores `broad`/`granular`
# (_VALID_INTEREST_KEYS), while ranking.py:466-467 already RENDERS those two
# lists to the model as "(domain)" and "(topic)" — so the ladder is live in the
# prompt layer and only the file keys are behind. Fixtures are authored in the
# catalog vocabulary; this dict is the whole adapter.
#
# There is no ENTITY row and one is not faked. The interests file has two
# rungs. Entity-grade following lives on the thread machinery (NL-17-M1b/M1c)
# and a fresh persona follows nobody by law; writing entity concepts into
# `granular:` would file a third rung's concepts under the second rung's label
# and weight, which is precisely what NL-17's "one concept = one vocabulary,
# tag XOR entity" acceptance criterion forbids.
ALTITUDE_TO_YAML_KEY = {"domain": "broad", "topic": "granular"}

# The topic editor's own level vocabulary (server.topic_add), which is a THIRD
# spelling of the same two rungs: it takes "broad"/"specific" and maps
# "specific" -> "granular" itself. Named here so provisioning never guesses.
ALTITUDE_TO_EDITOR_LEVEL = {"domain": "broad", "topic": "specific"}

VALID_ROLES = {"near-neighbour-a", "near-neighbour-b", "off-distribution"}

# The one metered residual left after the 2026-07-25 discovery pause: the
# Sonar verification call in analysis._sonar_verify, reached from generate via
# analysis.run_analysis. Discovery itself is paused at code level (config.
# discovery_enabled defaults False), the doctor fires no paid probe, and this
# key is all that stands between a persona generate and a charge.
SONAR_KEY_ENV = "PERPLEXITY_API_KEY"


class PersonaError(Exception):
    """A persona fixture that cannot be honoured, or a persona that is not one.

    Loud by design: a persona tool that degraded to the founder's world, or ran
    with half a reader's tags applied, would produce audit material nobody can
    trust — which is worse than producing none.
    """


@dataclass
class Persona:
    """One org-authored reader. Every field comes from the fixture file."""
    slug: str
    display: str
    port: int
    role: str
    design_intent: str
    interests: Dict[str, List[str]]      # altitude -> tags, catalog vocabulary
    path: Path

    @property
    def domain_tags(self) -> List[str]:
        return list(self.interests.get("domain", []))

    @property
    def topic_tags(self) -> List[str]:
        return list(self.interests.get("topic", []))

    @property
    def all_tags(self) -> List[str]:
        return self.domain_tags + self.topic_tags

    def yaml_interests(self) -> Dict[str, List[str]]:
        """This persona's tags under the keys sources.yaml actually stores."""
        return {ALTITUDE_TO_YAML_KEY[alt]: list(tags)
                for alt, tags in self.interests.items()}

    def line(self) -> str:
        return (f"{self.slug}  ({self.role}) · {self.display} · "
                f"port {self.port} · {len(self.domain_tags)} domain / "
                f"{len(self.topic_tags)} topic")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def fixture_paths() -> List[Path]:
    """Every persona fixture on disk, sorted. Read-only; creates nothing."""
    if not PERSONAS_DIR.is_dir():
        return []
    return sorted(p for p in PERSONAS_DIR.glob("*.yaml"))


def slugs() -> List[str]:
    return [p.stem for p in fixture_paths()]


def load(slug: str) -> Persona:
    """Parse one fixture, validating every field. Raises PersonaError.

    Validation is unusually strict for a fixture format on purpose: these files
    decide what an audit looks at. A typo'd altitude key that silently dropped
    eight topic tags would produce a persona whose editions "prove" that
    personalization is shallow, when what actually happened is that the reader
    had no topics.
    """
    import yaml  # lazy: keeps this module importable pre-install

    path = PERSONAS_DIR / f"{slug}.yaml"
    if not path.exists():
        known = ", ".join(slugs()) or "(none)"
        raise PersonaError(
            f"no persona fixture named {slug!r} — expected {path} "
            f"(known personas: {known})")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PersonaError(f"{path.name}: not valid YAML ({exc})") from exc
    if not isinstance(raw, dict):
        # Name the FULL path: the likeliest cause is a stray .yaml sitting in
        # personas/ that is not a persona at all, and "top level must be a
        # mapping" without a location sends the operator to the wrong file.
        raise PersonaError(
            f"{path}: top level must be a mapping — if this is not a persona "
            f"fixture it does not belong in {PERSONAS_DIR}")

    unknown = set(raw) - {"slug", "display", "port", "role", "design_intent",
                          "interests"}
    if unknown:
        raise PersonaError(
            f"{path.name}: unknown key(s) {sorted(unknown)} — a persona "
            "fixture may only carry slug, display, port, role, design_intent, "
            "interests")

    declared = raw.get("slug")
    if declared != slug:
        raise PersonaError(
            f"{path.name}: declares slug {declared!r} but its filename says "
            f"{slug!r} — the filename is how every tool finds it, so the two "
            "must agree")
    # The slug becomes a profile directory name; borrow the one validator.
    paths.normalize_profile(slug)
    if slug == paths.DEFAULT_PROFILE:
        raise PersonaError(
            f"{path.name}: {paths.DEFAULT_PROFILE!r} is the founder's own "
            "profile and is never a persona")

    display = raw.get("display")
    if not isinstance(display, str) or not display.strip():
        raise PersonaError(f"{path.name}: `display` must be a non-empty string")

    port = raw.get("port")
    if not isinstance(port, int) or isinstance(port, bool) \
            or not 1024 <= port <= 65535:
        raise PersonaError(
            f"{path.name}: `port` must be an integer 1024-65535, got {port!r}")
    if port == FOUNDER_PORT:
        raise PersonaError(
            f"{path.name}: port {FOUNDER_PORT} is the founder's own instance "
            "— a persona must never be reachable at his address")

    role = raw.get("role")
    if role not in VALID_ROLES:
        raise PersonaError(
            f"{path.name}: `role` must be one of {sorted(VALID_ROLES)}, got "
            f"{role!r}")

    intent = raw.get("design_intent")
    if not isinstance(intent, str) or not intent.strip():
        raise PersonaError(
            f"{path.name}: `design_intent` is required — a fixture whose "
            "purpose is undocumented cannot be audited against its purpose")

    raw_interests = raw.get("interests")
    if not isinstance(raw_interests, dict) or not raw_interests:
        raise PersonaError(
            f"{path.name}: `interests` must be a mapping of catalog altitudes "
            f"({'/'.join(ALTITUDE_TO_YAML_KEY)}) to tag lists")
    bad_alt = set(raw_interests) - set(ALTITUDE_TO_YAML_KEY)
    if bad_alt:
        raise PersonaError(
            f"{path.name}: unknown interest altitude(s) {sorted(bad_alt)}. The "
            f"catalog rungs a persona may use are "
            f"{sorted(ALTITUDE_TO_YAML_KEY)}; 'broad'/'granular' are the "
            "storage keys and are never written in a fixture, and the entity "
            "rung has no home in the interests file (see module docstring)")
    interests: Dict[str, List[str]] = {}
    for alt, tags in raw_interests.items():
        if not isinstance(tags, list) or not tags or not all(
                isinstance(t, str) and t.strip() for t in tags):
            raise PersonaError(
                f"{path.name}: interests.{alt} must be a non-empty list of "
                "non-empty strings")
        cleaned = [t.strip() for t in tags]
        lowered = [t.lower() for t in cleaned]
        if len(set(lowered)) != len(lowered):
            raise PersonaError(
                f"{path.name}: interests.{alt} repeats a tag — a duplicate is "
                "silently dropped by the topic editor, so the fixture and the "
                "provisioned world would disagree")
        interests[alt] = cleaned

    return Persona(slug=slug, display=display.strip(), port=port, role=role,
                   design_intent=intent.strip(), interests=interests,
                   path=path)


def load_all() -> List[Persona]:
    """Every fixture, parsed, with cross-fixture invariants enforced."""
    people = [load(s) for s in slugs()]
    ports: Dict[int, str] = {}
    for p in people:
        if p.port in ports:
            raise PersonaError(
                f"personas {ports[p.port]!r} and {p.slug!r} both claim port "
                f"{p.port} — two instances cannot share an address, and the "
                "second would fail to bind long after the operator stopped "
                "watching")
        ports[p.port] = p.slug
    return people


def require(slug: str) -> Persona:
    """Resolve a persona name or refuse loudly — including the founder."""
    if slug == paths.DEFAULT_PROFILE:
        raise PersonaError(
            f"{paths.DEFAULT_PROFILE!r} is the founder's own profile, not a "
            "persona. His world is reached the way it always was: "
            "`newslens serve` / `newslens generate`.")
    persona = load(slug)
    load_all()          # cross-fixture invariants (port collisions) still bite
    return persona


# ---------------------------------------------------------------------------
# Probe geometry — the kill-test's pre-registered numbers, computed not claimed
# ---------------------------------------------------------------------------

def _jaccard(a: List[str], b: List[str]) -> Tuple[int, int, float]:
    sa = {t.lower() for t in a}
    sb = {t.lower() for t in b}
    inter, union = len(sa & sb), len(sa | sb)
    return inter, union, (inter / union if union else 0.0)


def overlap(a: Persona, b: Persona) -> Dict[str, Tuple[int, int, float]]:
    """(shared, union, Jaccard) per rung between two personas.

    The kill-test's geometry is a measurement, not a comment: README §Probe
    quotes these numbers and a test recomputes them, so a later tag edit that
    quietly flattened the pair cannot pass unnoticed.
    """
    return {"domain": _jaccard(a.domain_tags, b.domain_tags),
            "topic": _jaccard(a.topic_tags, b.topic_tags)}


# ---------------------------------------------------------------------------
# The $0 contract
# ---------------------------------------------------------------------------

def zero_dollar_env(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The environment a persona pipeline runs in. $0 charged, honestly.

    THE KEY IS SET EMPTY, NOT DELETED, AND THE DIFFERENCE IS THE WHOLE POINT.

    Every real entrypoint calls `config.load_env()`, which is
    `load_dotenv(paths.ENV_FILE, override=False)`. python-dotenv's override
    check is literally `if k in os.environ and not self.override: continue`
    (dotenv/main.py, set_as_environment_variables) — it tests PRESENCE, not
    truthiness. So:

      * `env -u PERPLEXITY_API_KEY newslens ... generate`  ->  the variable is
        ABSENT from os.environ, load_env re-injects the principal's live key
        from .env, and analysis._sonar_verify fires a METERED Sonar call. The
        obvious spelling of "remove the key" is the one that spends money.
      * `PERPLEXITY_API_KEY= newslens ... generate`        ->  the variable is
        PRESENT and empty, load_env leaves it alone, and every consumer reads
        it through `(env.get(...) or "").strip()`, so it is absent everywhere
        it matters and _sonar_verify returns its honest
        "skipped — no PERPLEXITY_API_KEY".

    His `.env` is never touched. This is env-scoped, per invocation, and it
    expires with the process.

    What this does NOT do is fake a verification. The skip is disclosed: the
    status string rides the per-story report into the run record and the CLI
    prints it. A persona edition that could not verify a claim says so.
    """
    env = dict(os.environ if base is None else base)
    env[SONAR_KEY_ENV] = ""
    return env


def scrub_this_process() -> None:
    """Apply the $0 scrub to THIS process's own environment.

    THE one implementation, and every persona-lane entrypoint calls it. Same
    present-but-empty mechanism as `zero_dollar_env`, for the same reason —
    deleting the variable hands it straight back via `load_dotenv`.

    In-process is what makes it structural rather than advisory. A long-lived
    door (serve) hosts a web UI whose "Generate today's edition" button runs
    `generate.run_generate` INSIDE this process against `os.environ` — so the
    empty value set here dominates every generation that UI can start, without
    the button, the handler or the job runner needing to know this milestone
    exists. Anything that reads the key later reads the scrub.
    """
    os.environ[SONAR_KEY_ENV] = ""


def enforce_zero_dollar(slug: str) -> Optional[str]:
    """Scrub this process and MEASURE that it took. None = clear to proceed;
    otherwise the refusal sentence the caller prints before doing nothing.

    Both spend-reachable doors (serve, generate) go through here, so the
    contract cannot hold on one door and lapse on the other — which is exactly
    what the QA pass found: generate scrubbed, serve did not, and the persona's
    own day-one page carries a one-click Generate button.

    Also refuses an UNPAUSED tier-2 discovery: that is a second metered path,
    and a readiness check that reported it while the run door ignored it was an
    inconsistency waiting to become a bill (QA-7, related). NOTE: this arm was
    CLAIMED in the fix return before it existed (QA-8) — the sentence you are
    reading described nothing until the gate landed the check below, born red.
    """
    scrub_this_process()
    # profile_layout is PURE and IGNORES NEWSLENS_ENV_FILE, while the run
    # itself loads paths.ENV_FILE, which HONOURS it — so probe and run can name
    # different files (QA-7). Harmless today because the in-process empty value
    # above dominates whichever file is read; it stops being harmless the day
    # this door runs the verb in a CHILD with a filtered environment, at which
    # point the child's environment must carry the scrub too.
    env_file = paths.profile_layout(slug)["ENV_FILE"]
    try:
        # ONE child, BOTH facts: the probe already measures the key and the
        # discovery pause after load_env; asking for the key alone was how the
        # docstring's discovery claim went unlanded (QA-8).
        probe = pipeline_probe(dict(os.environ), env_file)
    except PersonaError as exc:
        return str(exc)
    n = int(probe["key_len"])
    if n != 0:
        return (f"REFUSING — the pipeline still resolves a {n}-character "
                f"{SONAR_KEY_ENV} after load_env, so this could fire a metered "
                "Sonar call. Nothing was run.")
    if probe.get("discovery_enabled"):
        return ("REFUSING — tier-2 discovery resolves UNPAUSED after load_env "
                "(the NEWSLENS_DISCOVERY_ENABLED opt-in is set), and discovery "
                "is a second metered Sonar path. The 2026-07-25 ruling pauses "
                "it on the default path; unset the opt-in to run a persona at "
                "$0. Nothing was run.")
    return None


# The child program that answers "what does the PIPELINE actually see?" — it
# runs `config.load_env()` exactly as every real entrypoint does, then reports
# the three numbers the $0 promise rests on. A claim about env plumbing that is
# not measured through a process boundary is a claim about a mental model of
# dotenv; and a cap or a pause read BEFORE load_env is a reading of the shell,
# not of the run (.env is where BUDGET_CAP_USD_PER_RUN actually lives).
#
# The key is reported as a LENGTH. That is all any caller needs and it is the
# only shape that cannot leak a credential into a report or a log.
# The .env path is passed IN, never resolved by the child. That is not a
# convenience: `paths.ENV_FILE` is a guarded attribute, so a child that asked
# for it would have to sanction itself with allow_real_paths() — and an ad-hoc
# probe process granting itself real paths is the exact 2026-07-14 incident the
# guard exists to prevent. `paths.profile_layout(slug)["ENV_FILE"]` is PURE
# (creates nothing, sanctions nothing, stats nothing) and returns the very path
# the sanctioned arm would return, so the parent names it and the child only
# reads it. `config.load_env(path)` takes the argument already.
#
# Found by running it: the first cut called bare `config.load_env()` and every
# real invocation died on the guard, while the suite passed because its fixture
# happened to export NEWSLENS_ENV_FILE (a redirection, which short-circuits the
# guard). The check refused to report green — which is the behaviour it was
# built for — but the plumbing was wrong and only a real run said so.
_PIPELINE_PROBE = """
import json, os, sys
sys.path.insert(0, {src!r})
from newslens import config
config.load_env({env_file!r})
out = {{"key_len": len((os.environ.get({key!r}) or "").strip()),
       "discovery_enabled": config.discovery_enabled(),
       "cap": None, "cap_error": None}}
try:
    out["cap"] = config.budget_cap_usd_per_run()
except Exception as exc:
    out["cap_error"] = "{{}}: {{}}".format(type(exc).__name__, exc)
print("PROBE " + json.dumps(out))
"""


def pipeline_probe(env: Dict[str, str], env_file: "Path | str",
                   python: Optional[str] = None) -> Dict[str, object]:
    """What the pipeline resolves under `env`, measured in a real child.

    Returns {"key_len", "discovery_enabled", "cap", "cap_error"} — every field
    read AFTER `config.load_env()`, which is the only moment at which any of
    them is true (BUDGET_CAP_USD_PER_RUN lives in .env; reading it before the
    load reads the shell, not the run). `key_len == 0` means the Sonar key is
    absent to every consumer and `analysis._sonar_verify` takes its honest skip
    path.

    Read-only by construction: the child loads a dotenv file and prints three
    numbers. It never resolves a guarded path and never sanctions itself.
    """
    src = str(paths.PROJECT_ROOT / "src")
    proc = subprocess.run(
        [python or sys.executable, "-c",
         _PIPELINE_PROBE.format(src=src, key=SONAR_KEY_ENV,
                                env_file=str(env_file))],
        env=env, capture_output=True, text=True, timeout=120,
        cwd=str(paths.PROJECT_ROOT))
    if proc.returncode != 0:
        raise PersonaError(
            "could not measure what the pipeline resolves — refusing to assume "
            f"the key is absent (exit {proc.returncode}): "
            f"{proc.stderr.strip()[:400]}")
    import json
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("PROBE "):
            return json.loads(line[len("PROBE "):])
    raise PersonaError(f"pipeline probe returned no verdict: {proc.stdout!r}")


def resolved_key_length(env: Dict[str, str], env_file: "Path | str",
                        python: Optional[str] = None) -> int:
    """How many characters of Sonar key the pipeline resolves under `env`. 0 =
    absent everywhere it matters. Never returns or prints the key itself."""
    return int(pipeline_probe(env, env_file, python)["key_len"])


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------

@dataclass
class ProvisionResult:
    slug: str
    created: bool = False
    applied: List[str] = field(default_factory=list)      # "domain:Foo"
    failures: List[str] = field(default_factory=list)     # "topic:Bar — msg"

    @property
    def ok(self) -> bool:
        return self.created and not self.failures


def provision(persona: Persona) -> ProvisionResult:
    """Mint one persona's world: `profiles.create` then the shipped topic editor.

    Two shipped lanes, no new machinery:

      * `profiles.create(slug)` gives the fully-migrated DB (through the CURRENT
        head migration, whatever it is — db.migrate applies every file in
        migrations/), the 0-byte memory.md, and the committed source catalog
        with an EMPTY interests block.
      * `server.topic_add` writes each tag, one at a time, through the editor
        the reader's own UI uses — including its validate-and-restore wrapper,
        so a tag that would break the file leaves the file unbroken and says so.

    The profile pin is set for the duration and CLEARED afterwards. That is not
    tidiness: `paths.SOURCES_FILE` resolves through `current_profile()`, so a
    leaked pin would send the NEXT caller's edit into this persona's world —
    the exact in-process leak M1's own CLI probe found and the doctor repeated.

    A partial application is reported, never swallowed and never auto-cleaned:
    the half-authored world stays on disk where the operator can see it, and
    the caller exits nonzero.
    """
    from . import profiles, server              # lazy: server is a big import

    if persona.slug == paths.DEFAULT_PROFILE:   # belt; profiles.create braces
        raise PersonaError("refusing to provision over the founder's profile")

    # QA-2: EVERY precondition resolves BEFORE anything is minted.
    # current_profile() RAISES on a malformed NEWSLENS_PROFILE (paths.py:152),
    # and it used to be read after profiles.create — so a bad env var produced
    # a refusal standing on a world that already existed, with zero tags,
    # which the operator's natural retry then reported as "already exists" and
    # exit 0. Reading it first makes the check a precondition again.
    previous = paths.current_profile()

    result = ProvisionResult(slug=persona.slug)
    profiles.create(persona.slug)
    result.created = True

    paths.set_profile(persona.slug)
    try:
        for altitude, tags in persona.interests.items():
            level = ALTITUDE_TO_EDITOR_LEVEL[altitude]
            for tag in tags:
                ok, msg = server.topic_add(tag, level)
                if ok:
                    result.applied.append(f"{altitude}:{tag}")
                else:
                    result.failures.append(f"{altitude}:{tag} — {msg}")
    finally:
        paths.set_profile(None if previous == paths.DEFAULT_PROFILE else previous)
    return result


# ---------------------------------------------------------------------------
# $0 first-briefing readiness
# ---------------------------------------------------------------------------

@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def readiness(persona: Persona,
              env: Optional[Dict[str, str]] = None) -> List[Check]:
    """Can this persona's FIRST briefing run, subscription-lane, at $0?

    Every check is observed. Nothing here runs a generate, spends a cent, or
    writes to the persona's world — it is the pre-flight, not the flight.
    """
    from . import config, db, profiles

    checks: List[Check] = []
    layout = paths.profile_layout(persona.slug)

    # --- the world exists and is the shipped lane's output -----------------
    exists = profiles.exists(persona.slug)
    checks.append(Check("world exists", exists,
                        str(paths.profile_root(persona.slug))))
    if not exists:
        checks.append(Check("readiness", False,
                            "provision it first: scripts/persona-provision "
                            f"{persona.slug}"))
        return checks

    st = profiles.status(persona.slug)

    # --- migrated through the CURRENT head, not a stale cut ----------------
    head = db.migration_files()[-1].name if db.migration_files() else "(none)"
    pending = st.pending_migrations
    applied_head = False
    if st.db_exists:
        try:
            con = db.connect_readonly(layout["DB_PATH"])
            try:
                applied_head = head in set(db.applied_migrations(con))
            finally:
                con.close()
        except Exception as exc:                                  # noqa: BLE001
            checks.append(Check("schema readable", False,
                                f"{type(exc).__name__}: {exc}"))
    checks.append(Check(
        f"schema at head ({head})",
        bool(st.db_exists and pending == 0 and applied_head),
        f"pending={pending}, head applied={applied_head}"))

    # --- commissioned: this reader has chosen tags -------------------------
    checks.append(Check("interests set (rank will not refuse)",
                        bool(st.has_interests),
                        f"commissioned={st.commissioned}"))
    try:
        cfg = config.load_sources(layout["SOURCES_FILE"])
        got = {"domain": cfg.interests_broad, "topic": cfg.interests_granular}
        missing = [f"{alt}:{t}" for alt, tags in persona.interests.items()
                   for t in tags if t not in got[alt]]
        checks.append(Check(
            "fixture tags all landed", not missing,
            f"{len(cfg.interests_broad)} domain / "
            f"{len(cfg.interests_granular)} topic"
            + (f"; MISSING {missing}" if missing else "")))
        checks.append(Check(
            "sources.yaml parses clean", not cfg.problems,
            "; ".join(cfg.problems) if cfg.problems else "no problems"))
        checks.append(Check(
            "active sources present", bool(cfg.sources),
            f"{len([s for s in cfg.sources if s.enabled])} enabled of "
            f"{len(cfg.sources)}"))
    except Exception as exc:                                      # noqa: BLE001
        checks.append(Check("sources.yaml parses clean", False,
                            f"{type(exc).__name__}: {exc}"))

    # --- cold start intact: nothing inherited ------------------------------
    checks.append(Check("memory.md is the 0-byte unseeded start",
                        st.memory_exists and st.memory_bytes == 0,
                        f"exists={st.memory_exists}, bytes={st.memory_bytes}"))
    checks.append(Check("no threads inherited", st.threads_active == 0,
                        f"active threads={st.threads_active}"))

    # --- per-profile ledger + cap armed ------------------------------------
    ledger = layout["DATA_DIR"] / "generation_log.jsonl"
    checks.append(Check("spend ledger is this profile's own",
                        paths.profile_root(persona.slug) in ledger.parents,
                        str(ledger)))
    # --- what the RUN will see: cap, pause and key, all after load_env -----
    # One child, three numbers. Reading any of them in this process would read
    # the shell instead of the run: BUDGET_CAP_USD_PER_RUN lives in .env, and
    # .env is not loaded until an entrypoint calls config.load_env().
    src_env = zero_dollar_env(env)
    try:
        probe = pipeline_probe(src_env, layout["ENV_FILE"])
    except PersonaError as exc:
        checks.append(Check("pipeline environment measurable", False, str(exc)))
        return checks

    cap, cap_error = probe.get("cap"), probe.get("cap_error")
    checks.append(Check(
        "per-run budget cap armed",
        bool(cap_error is None and isinstance(cap, (int, float)) and cap > 0),
        cap_error or f"BUDGET_CAP_USD_PER_RUN resolves to ${cap:.2f} "
                     "(caps bind SHADOW on the subscription lane — NL-95)"))
    checks.append(Check(
        "tier-2 discovery paused (code level)",
        not probe.get("discovery_enabled"),
        "no metered discovery call on the default path (ruling 2026-07-25)"))
    n = int(probe.get("key_len", -1))
    checks.append(Check(
        "Sonar key absent to the pipeline (after load_env, in a child)",
        n == 0,
        "resolved key length 0 — analysis._sonar_verify takes its honest "
        "skip path and discloses it" if n == 0 else
        f"resolved key length {n} — THE SCRUB DID NOT HOLD; a persona "
        "generate would fire a metered Sonar call"))

    # --- the founder is not in the blast radius ----------------------------
    founder = paths.profile_layout(paths.DEFAULT_PROFILE)
    shared = {k for k in ("DATA_DIR", "DB_PATH", "MEMORY_FILE", "SOURCES_FILE")
              if layout[k] == founder[k]}
    checks.append(Check("no path shared with the founder", not shared,
                        f"shared={sorted(shared)}" if shared
                        else ".env only (machine credentials, by design)"))
    return checks


# ---------------------------------------------------------------------------
# Entrypoints (scripts/persona-* are thin launchers over these)
#
# serve and generate DELEGATE to cli.main rather than re-implementing an
# entrypoint: that way the incident guard, the profile boundary, the
# redirection disclosure and the verb itself are all the shipped code, and this
# module adds no second copy of any of them. Only `provision` self-sanctions,
# because the shipped topic editor writes through the guarded SOURCES_FILE.
# ---------------------------------------------------------------------------

_WALL = ("These are ORG-AUTHORED QUALITY-AUDIT FIXTURES. Not users, not "
         "engagement evidence. See personas/README.md.")

# Guard against an exec loop if the venv interpreter ALSO lacks the dependency.
_REEXEC_MARKER = "NEWSLENS_PERSONA_REEXEC"


def _needs_reexec(prefix: str, venv_dir: "Path", marker: str) -> bool:
    """Should this process hand itself over to the checkout's venv?

    Compares venv ROOTS (`sys.prefix`), never resolved interpreter paths. The
    first cut compared `Path(sys.executable).resolve()` against the venv's
    python — and a venv's `bin/python` is a symlink chain ending at the SAME
    system binary, so the comparison was always False and the whole branch was
    unreachable (QA-4). `sys.prefix` is the one value that actually differs:
    inside a venv it IS the venv directory, outside it is the base install.

    Pure and total so the decision is testable without spawning anything —
    an untested `os.execve` is how a branch gets to claim behaviour it has
    never performed.
    """
    if marker == "1":
        return False                      # a second attempt would loop
    return Path(prefix).resolve() != Path(venv_dir).resolve()


def _ensure_runtime() -> None:
    """Re-exec into the project venv when the current interpreter cannot run.

    `scripts/persona-*` carry `#!/usr/bin/env python3` like every other
    launcher in this checkout, and the system python has no PyYAML. The
    existing launchers surface that as a raw ModuleNotFoundError traceback;
    these ones re-exec into `.venv/bin/python` instead, and if that is not
    possible they say which interpreter to use. A tool whose failure mode is a
    stack trace about `yaml` teaches nothing about what to do next.

    This module stays stdlib-only at IMPORT time (db.py's rule) — the import of
    yaml is lazy, inside load() — so the launcher can always reach this
    function; the check happens here, once, before any real work.
    """
    try:
        import yaml            # noqa: F401
        return
    except ImportError:
        pass
    venv_dir = paths.PROJECT_ROOT / ".venv"
    venv = venv_dir / "bin" / "python"
    if venv.exists() and _needs_reexec(sys.prefix, venv_dir,
                                       os.environ.get(_REEXEC_MARKER, "")):
        env = dict(os.environ, **{_REEXEC_MARKER: "1"})
        os.execve(str(venv), [str(venv)] + sys.argv, env)   # never returns
    print(f"persona: this tool needs the project's dependencies (PyYAML) and "
          f"{sys.executable} does not have them. Run it with the checkout's "
          f"interpreter:\n    {venv} {' '.join(sys.argv)}", file=sys.stderr)
    raise SystemExit(2)


def _resolve(argv: List[str], what: str) -> Tuple[Optional[Persona], int]:
    """Shared argument handling for the single-persona tools."""
    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        known = ", ".join(slugs()) or "(no fixtures found)"
        print(f"usage: scripts/persona-{what} <persona>\n  personas: {known}",
              file=sys.stderr)
        return None, 2
    try:
        return require(argv[0]), 0
    except (PersonaError, paths.ProfileError) as exc:
        print(f"persona: {exc}", file=sys.stderr)
        return None, 2


def _disclose_environment(slug: str) -> None:
    """Say out loud anything in the environment that could make this command
    mean something other than what it says.

    Two classes, both observed live in earlier milestones: an exported
    NEWSLENS_PROFILE naming a different reader (harmless here because the
    persona is pinned explicitly, but a reader who believes it is steering
    deserves to know it is not), and a path redirection, which OUTRANKS the
    profile and can hand this persona another world's file one path at a time.
    """
    from . import profiles

    env_profile = (os.environ.get(paths.PROFILE_ENV) or "").strip()
    if env_profile and env_profile != slug:
        print(f"note: {paths.PROFILE_ENV}={env_profile} is set and is being "
              f"IGNORED — this command names persona {slug!r} explicitly.",
              file=sys.stderr)
    for line in profiles.redirection_warnings(slug):
        print(f"warning: {line}", file=sys.stderr)


def provision_main(argv: Optional[List[str]] = None) -> int:
    """Mint persona worlds from the committed fixtures."""
    _ensure_runtime()
    scrub_this_process()       # no spend path here today; uniform by rule
    args = list(sys.argv[1:] if argv is None else argv)

    if not args or args[0] in ("-h", "--help"):
        print("usage: scripts/persona-provision [--all | --list | <persona>...]",
              file=sys.stderr)
        return 2
    try:
        people = load_all()
    except PersonaError as exc:
        print(f"persona fixtures: {exc}", file=sys.stderr)
        return 2

    if args == ["--list"]:
        from . import profiles
        print(_WALL + "\n")
        for p in people:
            state = "PROVISIONED" if profiles.exists(p.slug) else "not created"
            print(f"  {p.line()} · {state}")
        return 0

    if args == ["--all"]:
        wanted = people
    else:
        by_slug = {p.slug: p for p in people}
        wanted = []
        for name in args:
            if name not in by_slug:
                print(f"persona: no fixture named {name!r} (known: "
                      f"{', '.join(by_slug) or 'none'})", file=sys.stderr)
                return 2
            wanted.append(by_slug[name])

    print(_WALL + "\n")
    rc = 0
    from . import profiles
    # QA-6: sanction as late as its justification allows. The shipped topic
    # editor writes through the guarded SOURCES_FILE, so this is needed — but
    # only from here. --help and --list, which write nothing, now return above
    # without ever widening the incident guard.
    paths.allow_real_paths()
    for persona in wanted:
        _disclose_environment(persona.slug)
        if profiles.exists(persona.slug):
            # Never re-provision in place: a fresh empty DB beside a populated
            # memory.md is the file-and-DB disagreement NL-81 refuses.
            print(f"  {persona.slug}: already exists — left untouched "
                  f"({paths.profile_root(persona.slug)})")
            continue
        try:
            res = provision(persona)
        except Exception as exc:                                  # noqa: BLE001
            print(f"  {persona.slug}: FAILED — {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            # QA-2, honesty half: a failure can leave a world behind (the
            # topic editor can fail AFTER profiles.create has minted one). A
            # message that stops at "FAILED" lets the operator's natural retry
            # hit "already exists — left untouched" and exit 0 over a zero-tag,
            # un-rankable reader. Say which of the two happened.
            if profiles.exists(persona.slug):
                print(f"  {persona.slug}: INCOMPLETE — the world WAS created "
                      "before this failure and is still on disk. Left there on "
                      "purpose; inspect it, then delete the directory by hand "
                      "to re-mint.", file=sys.stderr)
            else:
                print(f"  {persona.slug}: nothing was created.",
                      file=sys.stderr)
            rc = 1
            continue
        print(f"  {persona.slug}: created · {len(res.applied)} tag(s) applied "
              f"({len(persona.domain_tags)} domain / "
              f"{len(persona.topic_tags)} topic expected)")
        for failure in res.failures:
            print(f"      TAG REFUSED — {failure}", file=sys.stderr)
        if not res.ok:
            print(f"  {persona.slug}: INCOMPLETE — the world exists but its "
                  "reader's tags are only partly applied. Left on disk on "
                  "purpose; inspect it, then delete the directory by hand to "
                  "re-mint.", file=sys.stderr)
            rc = 1
    if rc == 0:
        print("\nnext: scripts/persona-ready --all")
    return rc


def ready_main(argv: Optional[List[str]] = None) -> int:
    """$0 first-briefing readiness. Read-only: spends nothing, writes nothing.

    Deliberately does NOT call allow_real_paths(): every observation it makes
    goes through an explicitly-named path (profile_layout, load_sources with a
    path argument) or a child process, so the guarded module attributes are
    never touched and this tool structurally cannot clobber anyone's world.
    """
    _ensure_runtime()
    scrub_this_process()       # no spend path here today; uniform by rule
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print("usage: scripts/persona-ready [--all | <persona>...]",
              file=sys.stderr)
        return 2
    try:
        people = load_all()
    except PersonaError as exc:
        print(f"persona fixtures: {exc}", file=sys.stderr)
        return 2
    if args == ["--all"]:
        wanted = people
    else:
        by_slug = {p.slug: p for p in people}
        missing = [a for a in args if a not in by_slug]
        if missing:
            print(f"persona: no fixture named {missing[0]!r}", file=sys.stderr)
            return 2
        wanted = [by_slug[a] for a in args]

    print(_WALL + "\n")
    rc = 0
    for persona in wanted:
        print(f"{persona.slug}  ({persona.role})  port {persona.port}")
        for check in readiness(persona):
            print(f"  [{'ok  ' if check.ok else 'FAIL'}] {check.name} — "
                  f"{check.detail}")
            if not check.ok:
                rc = 1
        print()
    print("READY — every persona can run its first briefing at $0" if rc == 0
          else "NOT READY — see the FAIL lines above", file=sys.stderr)
    return rc


def serve_main(argv: Optional[List[str]] = None) -> int:
    """Serve one persona's world on its own port. The founder's 8484 is never
    touched: this is a second instance, not a switch inside his."""
    _ensure_runtime()
    import socket

    persona, rc = _resolve(list(sys.argv[1:] if argv is None else argv), "serve")
    if persona is None:
        return rc
    from . import profiles
    if not profiles.exists(persona.slug):
        print(f"persona: {persona.slug!r} has no world yet — run "
              f"`scripts/persona-provision {persona.slug}` first",
              file=sys.stderr)
        return 2
    # Refuse a busy port with a sentence instead of a traceback out of
    # ThreadingHTTPServer, and never let a persona land on the founder's port.
    if persona.port == FOUNDER_PORT:
        print(f"persona: refusing port {FOUNDER_PORT} — that is the founder's "
              "own instance", file=sys.stderr)
        return 2
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if probe.connect_ex(("127.0.0.1", persona.port)) == 0:
            print(f"persona: port {persona.port} is already serving — "
                  f"{persona.slug} may already be running at "
                  f"http://127.0.0.1:{persona.port}/", file=sys.stderr)
            return 2
    finally:
        probe.close()

    # THE $0 CONTRACT ON THE SERVE DOOR (QA-1, HIGH). Not decoration: this
    # process is about to host a web UI whose empty-state page carries a
    # one-click "Generate today's edition" button (server.py:1895 ->
    # webui.py:1442 -> /api/generate -> GEN_JOB.start -> generate.run_generate
    # against os.environ). Without the scrub, load_env injects the principal's
    # live key into THIS process and that button spends his money. Scrubbing
    # here means every generation the UI can start is already $0.
    refusal = enforce_zero_dollar(persona.slug)
    if refusal is not None:
        print(f"persona: {refusal}", file=sys.stderr)
        return 1

    _disclose_environment(persona.slug)
    print(f"persona {persona.slug} — {persona.display} ({persona.role})")
    print(f"{SONAR_KEY_ENV} scrubbed for this process only (verified absent "
          "after load_env) — including anything this instance's UI can start.")
    print(_WALL)
    from . import cli
    return cli.main(["--profile", persona.slug, "serve",
                     "--port", str(persona.port)])


def generate_main(argv: Optional[List[str]] = None) -> int:
    """One persona's briefing, subscription-lane, $0 charged.

    The env scrub is applied and then MEASURED before the run starts. A
    readiness claim that is not measured is exactly the claim that would let a
    metered call through: see zero_dollar_env for why the obvious spelling of
    "remove the key" is the one that spends money.
    """
    _ensure_runtime()
    args = list(sys.argv[1:] if argv is None else argv)
    passthrough = [a for a in args if a.startswith("-")]
    names = [a for a in args if not a.startswith("-")]
    persona, rc = _resolve(names, "generate")
    if persona is None:
        return rc
    from . import profiles
    if not profiles.exists(persona.slug):
        print(f"persona: {persona.slug!r} has no world yet — run "
              f"`scripts/persona-provision {persona.slug}` first",
              file=sys.stderr)
        return 2

    # Scope the scrub to THIS process (and therefore to the run), through the
    # SAME helper the serve door uses. The principal's .env is never read for
    # writing, never edited, never copied.
    refusal = enforce_zero_dollar(persona.slug)
    if refusal is not None:
        print(f"persona: {refusal}", file=sys.stderr)
        return 1

    _disclose_environment(persona.slug)
    print(f"persona {persona.slug} — {persona.display} ({persona.role})")
    print(f"{SONAR_KEY_ENV} scrubbed for this process only (verified absent "
          "after load_env); tier-2 discovery paused; per-profile cap + ledger.")
    print(_WALL)
    from . import cli
    return cli.main(["--profile", persona.slug, "generate"] + passthrough)
