"""Load wikibackup's settings: policy from ``config.toml`` and machine paths from ``.env``.

Every subcommand in ``cli`` starts by calling ``load_config``. The result tells
``catalog`` which books to look up, and tells ``library`` where the archive (``DATA_DIR``),
the hot copy (``HOT_DIR``) and the generated ``library.xml`` (``STATE_DIR``) live. See
"Where things live" in ``docs/kiwix_backup_plan.md``.

Sources and precedence:

- ``config.toml`` (committed) gives ``[policy]`` and one ``[[zim]]`` table per tracked book.
  It's required.
- ``.env`` (not committed) gives machine settings as ``KEY=VALUE`` lines. It's optional;
  any key it leaves out takes its default from ``ENV_DEFAULTS``.
- A variable set in the process environment overrides the same key in ``.env``.

Both files are looked up in the repo root by default. The root is found from this file's
location (``src/wikibackup/config.py``), which assumes the package runs from its source
checkout, as ``uv run`` does. Nothing here touches the data drives.
"""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

# The repo root: src/wikibackup/config.py -> parents[2].
REPO_ROOT = Path(__file__).resolve().parents[2]

# Defaults for .env keys. These match .env.example; keep them in step.
ENV_DEFAULTS = {
    "DATA_DIR": "/Volumes/Starfish_Prime/kiwix-data",
    "HOT_DIR": "/Volumes/BackUp_T7/kiwix-hot",
    "STATE_DIR": "~/.local/state/wikibackup",
    "BIND_ADDRESS": "all",
    "PORT": "8080",
    "LOG_LEVEL": "INFO",
}


class ConfigError(Exception):
    """A config file is missing or has a missing or invalid value."""


@dataclass(frozen=True)
class ZimSpec:
    """One tracked book: the catalog's ``<name>`` and ``<flavour>`` (e.g. ``wikipedia_en_all``
    and ``maxi``). Its ZIM files are named ``<name>_<flavour>_<YYYY-MM>.zim``."""

    name: str
    flavour: str

    @property
    def file_prefix(self) -> str:
        """The file name up to the date, e.g. ``wikipedia_en_all_maxi_``."""
        return f"{self.name}_{self.flavour}_"


@dataclass(frozen=True)
class Policy:
    """Update and retention policy from ``[policy]`` in ``config.toml``.

    ``min_age_months``: don't replace a local version younger than this many months.
    ``keep_versions``: how many versions of each book the archive keeps.
    """

    min_age_months: int
    keep_versions: int


@dataclass(frozen=True)
class Config:
    """All settings. Paths are absolute, with ``~`` expanded.

    ``hot_dir`` is ``None`` when ``HOT_DIR`` is empty, which turns the hot copy off.
    """

    policy: Policy
    zims: tuple[ZimSpec, ...]
    data_dir: Path
    hot_dir: Path | None
    state_dir: Path
    bind_address: str
    port: int
    log_level: str


def parse_env_file(text: str) -> dict[str, str]:
    """Parse ``.env`` text into a dict.

    Accepts ``KEY=VALUE`` lines; blank lines and lines starting with ``#`` are skipped.
    Whitespace around the key and the value is stripped, and one pair of matching quotes
    around the value is removed. Inline comments are **not** supported: ``PORT=8080 # x``
    gives the value ``8080 # x``. A line without ``=`` raises ``ConfigError``.
    """
    values = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise ConfigError(f".env line {lineno}: expected KEY=VALUE, got {raw!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _expand(path: str) -> Path:
    """Expand ``~`` and make the path absolute (without resolving symlinks)."""
    return Path(os.path.expanduser(path)).absolute()


def load_config(
    config_path: Path | None = None,
    env_path: Path | None = None,
    environ: dict[str, str] | None = None,
) -> Config:
    """Load and check all settings.

    ``config_path`` and ``env_path`` default to ``config.toml`` and ``.env`` in the repo
    root. ``environ`` defaults to ``os.environ``; tests pass a dict instead.

    Raises ``ConfigError`` if ``config.toml`` is missing, has no ``[[zim]]`` entries, or
    has a missing or invalid policy value, or if ``PORT`` isn't an integer.
    """
    config_path = config_path or REPO_ROOT / "config.toml"
    env_path = env_path or REPO_ROOT / ".env"
    environ = os.environ if environ is None else environ

    try:
        raw = tomllib.loads(config_path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {config_path}") from None

    try:
        policy = Policy(
            min_age_months=int(raw["policy"]["min_age_months"]),
            keep_versions=int(raw["policy"]["keep_versions"]),
        )
        zims = tuple(ZimSpec(name=z["name"], flavour=z["flavour"]) for z in raw["zim"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"{config_path}: missing or invalid setting: {exc}") from None
    if not zims:
        raise ConfigError(f"{config_path}: no [[zim]] entries")
    if policy.keep_versions < 1:
        raise ConfigError(f"{config_path}: keep_versions must be at least 1")

    env = dict(ENV_DEFAULTS)
    if env_path.exists():
        env.update(parse_env_file(env_path.read_text()))
    env.update({k: environ[k] for k in ENV_DEFAULTS if k in environ})

    try:
        port = int(env["PORT"])
    except ValueError:
        raise ConfigError(f"PORT must be an integer, got {env['PORT']!r}") from None

    return Config(
        policy=policy,
        zims=zims,
        data_dir=_expand(env["DATA_DIR"]),
        hot_dir=_expand(env["HOT_DIR"]) if env["HOT_DIR"] else None,
        state_dir=_expand(env["STATE_DIR"]),
        bind_address=env["BIND_ADDRESS"],
        port=port,
        log_level=env["LOG_LEVEL"],
    )
