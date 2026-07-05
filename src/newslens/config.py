"""Runtime configuration: .env loading + sources.yaml parsing.

This module owns the "refuse politely" rule (principal decision, DECISIONS.md
2026-07-02): NewsLens never invents outlets or interests. The shipped
sources.yaml is principal-seeded (37 outlets as of 2026-07-03); interests
arrive with M3's tag contract. If sources.yaml has no active sources, the
pipeline must refuse with NO_ACTIVE_SOURCES_MSG — it must never silently fall
back to sources the principal never chose.

This module is stdlib-only AT IMPORT TIME (like db.py/paths.py), so the doctor
can import it unconditionally pre-install and there is exactly ONE
implementation of the guard-var validators — the doctor renders these, it must
never re-implement them (QA fix loop 1: duplicated validation is how BUG-1
shipped). Third-party imports (yaml, dotenv) happen lazily inside the
functions that need them; callers that must survive a missing dep catch
ImportError around the call.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

from . import paths

NO_ACTIVE_SOURCES_MSG = (
    "sources.yaml has no active sources — uncomment or add your outlets"
)
NO_INTERESTS_MSG = (
    "sources.yaml has no interests yet — they steer ranking and the daily "
    "discovery query (add them under `interests:` when ready)"
)

# Defaults for the two no-account config vars (.env.example documents both).
DEFAULT_BUDGET_CAP_USD_PER_RUN = 0.50
DEFAULT_GENERATE_HOUR_LOCAL = 6

_VALID_SOURCE_KEYS = {
    "name", "rss_url", "wire_syndication", "tier", "enabled", "note",
    "followed_analyst",  # M3: personal-impact ranking boost for followed writers
}
_VALID_TOP_LEVEL_KEYS = {"sources", "interests", "settings"}
_VALID_INTEREST_KEYS = {"broad", "granular"}
_VALID_SETTINGS_KEYS = {"threads_steer_selection"}

# Source tiers (milestone 2, principal's source list):
#   full           — usable RSS content (title + summary/excerpt)
#   headline_only  — paywalled outlet (Bloomberg/WaPo/FT-class): titles +
#                    summaries only; attribution + linkout in briefings
#   cautious       — aggregators: DEFAULT-DISABLED; if enabled, flagged and
#                    down-weighted downstream (ranking, corroboration)
#   reference_only — citable in briefings, NEVER fetched (NYT, Wikipedia, AP,
#                    Reuters); rss_url optional and ignored
VALID_TIERS = ("full", "headline_only", "cautious", "reference_only")


class SourcesParseError(ValueError):
    """sources.yaml is missing, unreadable, or not valid YAML."""


@dataclass
class Source:
    name: str
    rss_url: Optional[str] = None
    tier: str = "full"
    enabled: bool = True
    wire_syndication: bool = False
    followed_analyst: bool = False  # taxonomy contract §A: personal-impact
    # credit for a followed writer's items independent of topic match
    note: str = ""

    @property
    def fetchable(self) -> bool:
        """Fetched by ingestion only if enabled, not reference-only, and has a
        URL. reference_only is structural: those outlets are never fetched
        regardless of flags (principal ruling in the M2 dispatch)."""
        return self.enabled and self.tier != "reference_only" and bool(self.rss_url)


@dataclass
class SourcesConfig:
    """Parsed sources.yaml. `problems` holds format errors that should be
    surfaced loudly (doctor renders them as failures) — a typo'd key must
    never be silently ignored. `warnings` are non-blocking flags (e.g. a
    cautious aggregator explicitly enabled)."""

    sources: List[Source] = field(default_factory=list)
    interests_broad: List[str] = field(default_factory=list)
    interests_granular: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    # A6 (principal editorial review 2026-07-05): briefings-of-record select
    # on tags + world impact only; thread recording/continuity continues
    # regardless. Principal-flippable in sources.yaml `settings:`.
    threads_steer_selection: bool = False

    @property
    def fetchable_sources(self) -> List[Source]:
        return [s for s in self.sources if s.fetchable]

    @property
    def reference_only_sources(self) -> List[Source]:
        return [s for s in self.sources if s.tier == "reference_only"]

    @property
    def disabled_sources(self) -> List[Source]:
        return [s for s in self.sources if s.tier != "reference_only" and not s.enabled]

    @property
    def has_active_sources(self) -> bool:
        return len(self.fetchable_sources) > 0

    @property
    def followed_analyst_sources(self) -> List[Source]:
        return [s for s in self.sources if s.followed_analyst]

    @property
    def has_interests(self) -> bool:
        return bool(self.interests_broad or self.interests_granular)


def load_env(env_file: Optional[Union[str, Path]] = None) -> None:
    """Load .env into os.environ. Real environment always wins (override=False).

    Missing .env is not an error — each consumer reports missing vars itself
    with a fix hint (the doctor is the friendly version of that).
    """
    from dotenv import load_dotenv

    load_dotenv(env_file or paths.ENV_FILE, override=False)


def _str_list(value: object, where: str, problems: List[str]) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) and x.strip() for x in value):
        problems.append(f"`{where}` must be a list of non-empty strings")
        return []
    return [x.strip() for x in value]


def load_sources(path: Optional[Union[str, Path]] = None) -> SourcesConfig:
    """Parse sources.yaml into a SourcesConfig.

    A fully-commented template (the shipped state) parses to YAML `None` and
    yields a config with zero active sources and zero problems — a valid,
    expected state that the pipeline refuses politely and the doctor flags as
    action-needed, not as breakage.
    """
    import yaml  # lazy: keeps this module importable pre-install (see docstring)

    p = Path(path) if path is not None else paths.SOURCES_FILE
    if not p.exists():
        raise SourcesParseError(f"sources file not found: {p}")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        # e.g. PermissionError. Must surface as the same friendly error class
        # as any other unusable sources file — never a raw traceback (BUG-2).
        raise SourcesParseError(
            f"sources.yaml exists but is not readable ({exc}) — check its file permissions"
        ) from exc
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SourcesParseError(f"sources.yaml is not valid YAML: {exc}") from exc

    cfg = SourcesConfig()
    if raw is None:
        return cfg  # everything still commented out — the shipped template state

    if not isinstance(raw, dict):
        cfg.problems.append(
            "top level must be a mapping with `sources:` and optional `interests:` "
            "(see the template comments in sources.yaml)"
        )
        return cfg

    for key in raw:
        if key not in _VALID_TOP_LEVEL_KEYS:
            cfg.problems.append(
                f"unknown top-level key `{key}` (did you mean `sources` or `interests`?)"
            )

    raw_sources = raw.get("sources")
    if raw_sources is not None:
        if not isinstance(raw_sources, list):
            cfg.problems.append("`sources` must be a list of `- name: … / rss_url: …` entries")
        else:
            for i, entry in enumerate(raw_sources, start=1):
                if not isinstance(entry, dict):
                    cfg.problems.append(f"source #{i} must be a mapping with name/rss_url")
                    continue
                for key in entry:
                    if key not in _VALID_SOURCE_KEYS:
                        cfg.problems.append(f"source #{i}: unknown key `{key}`")
                name = entry.get("name")
                if not isinstance(name, str) or not name.strip():
                    cfg.problems.append(f"source #{i}: `name` is required (non-empty string)")
                    continue
                name = name.strip()

                tier = entry.get("tier", "full")
                if not isinstance(tier, str) or tier not in VALID_TIERS:
                    cfg.problems.append(
                        f"source #{i} ({name!r}): `tier` must be one of {'|'.join(VALID_TIERS)}"
                    )
                    continue

                rss_url = entry.get("rss_url")
                if tier == "reference_only":
                    # Never fetched; a URL may be recorded for documentation only.
                    if rss_url is not None and not (
                        isinstance(rss_url, str)
                        and rss_url.strip().startswith(("http://", "https://"))
                    ):
                        cfg.problems.append(
                            f"source #{i} ({name!r}): `rss_url`, when present, must be an http(s) URL"
                        )
                        continue
                    rss_url = rss_url.strip() if isinstance(rss_url, str) else None
                else:
                    if not isinstance(rss_url, str) or not rss_url.strip().startswith(("http://", "https://")):
                        cfg.problems.append(
                            f"source #{i} ({name!r}): `rss_url` is required and must be an http(s) URL"
                        )
                        continue
                    rss_url = rss_url.strip()

                # Cautious aggregators are DEFAULT-DISABLED: omitting `enabled`
                # on a cautious source means off; enabling one is explicit.
                enabled = entry.get("enabled", tier != "cautious")
                if not isinstance(enabled, bool):
                    cfg.problems.append(f"source #{i} ({name!r}): `enabled` must be true or false")
                    continue

                wire = entry.get("wire_syndication", False)
                if not isinstance(wire, bool):
                    cfg.problems.append(
                        f"source #{i} ({name!r}): `wire_syndication` must be true or false"
                    )
                    continue

                note = entry.get("note", "")
                if not isinstance(note, str):
                    cfg.problems.append(f"source #{i} ({name!r}): `note` must be a string")
                    continue

                followed = entry.get("followed_analyst", False)
                if not isinstance(followed, bool):
                    cfg.problems.append(
                        f"source #{i} ({name!r}): `followed_analyst` must be true or false"
                    )
                    continue

                if tier == "cautious" and enabled:
                    cfg.warnings.append(
                        f"cautious source {name!r} is explicitly enabled — aggregator "
                        "content will be flagged and down-weighted downstream"
                    )

                cfg.sources.append(
                    Source(
                        name=name,
                        rss_url=rss_url,
                        tier=tier,
                        enabled=enabled,
                        wire_syndication=wire,
                        followed_analyst=followed,
                        note=note.strip(),
                    )
                )

            # Duplicate lint (M2 review carryover): a copy-pasted entry must be
            # a loud problem, not a silent double-fetch or double-count.
            seen_names: dict = {}
            seen_urls: dict = {}
            for s in cfg.sources:
                key = s.name.casefold()
                if key in seen_names:
                    cfg.problems.append(f"duplicate source name: {s.name!r}")
                else:
                    seen_names[key] = s.name
                if s.rss_url:
                    if s.rss_url in seen_urls:
                        cfg.problems.append(
                            f"duplicate rss_url on {s.name!r} and {seen_urls[s.rss_url]!r}: {s.rss_url}"
                        )
                    else:
                        seen_urls[s.rss_url] = s.name

    raw_settings = raw.get("settings")
    if raw_settings is not None:
        if not isinstance(raw_settings, dict):
            cfg.problems.append("`settings` must be a mapping")
        else:
            for key in raw_settings:
                if key not in _VALID_SETTINGS_KEYS:
                    cfg.problems.append(f"settings: unknown key `{key}`")
            tss = raw_settings.get("threads_steer_selection", False)
            if not isinstance(tss, bool):
                cfg.problems.append("settings.threads_steer_selection must be true or false")
            else:
                cfg.threads_steer_selection = tss

    raw_interests = raw.get("interests")
    if raw_interests is not None:
        if not isinstance(raw_interests, dict):
            cfg.problems.append("`interests` must be a mapping with `broad:` and/or `granular:` lists")
        else:
            for key in raw_interests:
                if key not in _VALID_INTEREST_KEYS:
                    cfg.problems.append(f"interests: unknown key `{key}` (use `broad` / `granular`)")
            cfg.interests_broad = _str_list(raw_interests.get("broad"), "interests.broad", cfg.problems)
            cfg.interests_granular = _str_list(raw_interests.get("granular"), "interests.granular", cfg.problems)

    return cfg


def require_active_sources(cfg: Optional[SourcesConfig] = None) -> List[Source]:
    """The pipeline's entry gate: returns the FETCHABLE sources (enabled,
    non-reference-only, with a URL) or raises with a message fit to show the
    principal directly. Never silently falls back to sources the principal
    didn't enable."""
    cfg = cfg if cfg is not None else load_sources()
    if cfg.problems:
        raise SourcesParseError("sources.yaml has problems: " + "; ".join(cfg.problems))
    if not cfg.has_active_sources:
        raise SourcesParseError(NO_ACTIVE_SOURCES_MSG)
    return cfg.fetchable_sources


def budget_cap_usd_per_run(env: Optional[dict] = None) -> float:
    """BUDGET_CAP_USD_PER_RUN with default; raises ValueError on anything that
    is not a positive FINITE number.

    Non-finite values are rejected explicitly (BUG-1): float() happily parses
    'nan'/'inf', and `nan <= 0` is False — but a nan cap makes every later
    `cost > cap` comparison False, so the ENGINEERING.md-mandated budget abort
    would never fire. A spend cap that cannot stop spending is garbage.

    THE single validator: the doctor renders this function's result or
    exception; it must never re-implement the rules (that duplication is how
    BUG-1 shipped in both copies).
    """
    src = env if env is not None else os.environ
    raw = (src.get("BUDGET_CAP_USD_PER_RUN") or "").strip()
    if not raw:
        return DEFAULT_BUDGET_CAP_USD_PER_RUN
    try:
        value = float(raw)
    except ValueError:
        value = None
    if value is None or not math.isfinite(value) or value <= 0:
        raise ValueError(
            f"BUDGET_CAP_USD_PER_RUN must be a positive number "
            f"(a finite USD amount per run, e.g. 0.50), got {raw!r}"
        )
    return value


def generate_hour_local(env: Optional[dict] = None) -> int:
    """GENERATE_HOUR_LOCAL with default; raises ValueError outside 0-23.

    Same single-validator rule as budget_cap_usd_per_run: the doctor renders
    this; it never re-implements it.
    """
    src = env if env is not None else os.environ
    raw = (src.get("GENERATE_HOUR_LOCAL") or "").strip()
    if not raw:
        return DEFAULT_GENERATE_HOUR_LOCAL
    try:
        value = int(raw)
    except ValueError:
        value = None
    if value is None or not 0 <= value <= 23:
        raise ValueError(
            f"GENERATE_HOUR_LOCAL must be an integer hour 0-23, got {raw!r}"
        )
    return value
