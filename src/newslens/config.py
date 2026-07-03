"""Runtime configuration: .env loading + sources.yaml parsing.

This module owns the "refuse politely" rule (principal decision, DECISIONS.md
2026-07-02): NewsLens ships with NO default outlets and NO default interests.
If sources.yaml has no active sources, the pipeline must refuse with
NO_ACTIVE_SOURCES_MSG — it must never silently fall back to sources the
principal never chose.

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

_VALID_SOURCE_KEYS = {"name", "rss_url", "wire_syndication"}
_VALID_TOP_LEVEL_KEYS = {"sources", "interests"}
_VALID_INTEREST_KEYS = {"broad", "granular"}


class SourcesParseError(ValueError):
    """sources.yaml is missing, unreadable, or not valid YAML."""


@dataclass
class Source:
    name: str
    rss_url: str
    wire_syndication: bool = False


@dataclass
class SourcesConfig:
    """Parsed sources.yaml. `problems` holds format errors that should be
    surfaced loudly (doctor renders them as failures) — a typo'd key must
    never be silently ignored."""

    sources: List[Source] = field(default_factory=list)
    interests_broad: List[str] = field(default_factory=list)
    interests_granular: List[str] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)

    @property
    def has_active_sources(self) -> bool:
        return len(self.sources) > 0

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
                rss_url = entry.get("rss_url")
                wire = entry.get("wire_syndication", False)
                if not isinstance(name, str) or not name.strip():
                    cfg.problems.append(f"source #{i}: `name` is required (non-empty string)")
                    continue
                if not isinstance(rss_url, str) or not rss_url.strip().startswith(("http://", "https://")):
                    cfg.problems.append(
                        f"source #{i} ({name!r}): `rss_url` is required and must be an http(s) URL"
                    )
                    continue
                if not isinstance(wire, bool):
                    cfg.problems.append(
                        f"source #{i} ({name!r}): `wire_syndication` must be true or false"
                    )
                    continue
                cfg.sources.append(Source(name=name.strip(), rss_url=rss_url.strip(), wire_syndication=wire))

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
    """The pipeline's entry gate (used from milestone 2 on): returns active
    sources or raises with a message fit to show the principal directly."""
    cfg = cfg if cfg is not None else load_sources()
    if cfg.problems:
        raise SourcesParseError("sources.yaml has problems: " + "; ".join(cfg.problems))
    if not cfg.has_active_sources:
        raise SourcesParseError(NO_ACTIVE_SOURCES_MSG)
    return cfg.sources


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
