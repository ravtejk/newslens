"""Canonical filesystem locations for the NewsLens prototype.

Everything is anchored on the repo checkout that contains this file
(src/newslens/paths.py -> prototype/). This assumes the package is used from
the checkout — either editable-installed (`pip install -e .`, the documented
setup) or run via scripts/doctor's sys.path bootstrap. A non-editable install
into site-packages is unsupported for this prototype (migrations/ and
sources.yaml live in the checkout, not in the wheel); the doctor script
verifies the anchor and says so rather than failing cryptically.

Stdlib-only by design (see module docstring in newslens/__init__.py).
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data"                 # gitignored; created on demand
DB_PATH = DATA_DIR / "newslens.db"
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
SOURCES_FILE = PROJECT_ROOT / "sources.yaml"
ENV_FILE = PROJECT_ROOT / ".env"                 # principal-edited; never committed
ENV_EXAMPLE_FILE = PROJECT_ROOT / ".env.example"


def looks_like_checkout() -> bool:
    """True if PROJECT_ROOT actually is the prototype checkout."""
    return (PROJECT_ROOT / "pyproject.toml").exists() and MIGRATIONS_DIR.is_dir()
