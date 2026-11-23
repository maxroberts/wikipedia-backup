"""Tests for ``wikibackup.config``: ``.env`` parsing, defaults, precedence and errors.

Each test writes its own ``config.toml`` and ``.env`` into pytest's ``tmp_path`` and passes
an explicit ``environ`` dict, so the real repo files and the shell environment are never
read.
"""

from pathlib import Path

import pytest

from wikibackup.config import ENV_DEFAULTS, REPO_ROOT, ConfigError, load_config, parse_env_file

GOOD_TOML = """
[policy]
min_age_months = 6
keep_versions = 3

[[zim]]
name = "wikipedia_en_all"
flavour = "maxi"

[kiwix_tools]
version = "3.8.2"
platforms = ["macos-arm64", "linux-x86_64"]
"""


def write(tmp_path: Path, toml: str = GOOD_TOML, env: str | None = None):
    """Write config.toml (and .env if given) into tmp_path; return both paths."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(toml)
    env_path = tmp_path / ".env"
    if env is not None:
        env_path.write_text(env)
    return config_path, env_path


def test_parse_env_file_handles_comments_blanks_and_quotes():
    text = "# comment\n\nA=1\n B = two words \nC=\"quoted\"\nD='single'\nE=\n"
    assert parse_env_file(text) == {
        "A": "1", "B": "two words", "C": "quoted", "D": "single", "E": "",
    }


def test_parse_env_file_rejects_line_without_equals():
    with pytest.raises(ConfigError, match="line 2"):
        parse_env_file("A=1\nnot a setting\n")


def test_defaults_when_no_env_file(tmp_path):
    config_path, env_path = write(tmp_path)
    config = load_config(config_path, env_path, environ={})
    assert config.policy.min_age_months == 6
    assert config.policy.keep_versions == 3
    assert [(z.name, z.flavour) for z in config.zims] == [("wikipedia_en_all", "maxi")]
    assert config.kiwix_tools.version == "3.8.2"
    assert config.kiwix_tools.platforms == ("macos-arm64", "linux-x86_64")
    assert config.data_dir == Path(ENV_DEFAULTS["DATA_DIR"])
    assert config.hot_dir == Path(ENV_DEFAULTS["HOT_DIR"])
    assert config.state_dir == Path.home() / ".local/state/wikibackup"
    assert config.port == 8080


def test_env_file_then_environment_override(tmp_path):
    config_path, env_path = write(tmp_path, env="DATA_DIR=/tmp/a\nPORT=9000\n")
    config = load_config(config_path, env_path, environ={"PORT": "9100", "UNRELATED": "x"})
    assert config.data_dir == Path("/tmp/a")
    assert config.port == 9100


def test_empty_hot_dir_turns_hot_copy_off(tmp_path):
    config_path, env_path = write(tmp_path, env="HOT_DIR=\n")
    assert load_config(config_path, env_path, environ={}).hot_dir is None


def test_bad_port(tmp_path):
    config_path, env_path = write(tmp_path, env="PORT=eighty\n")
    with pytest.raises(ConfigError, match="PORT"):
        load_config(config_path, env_path, environ={})


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml", tmp_path / ".env", environ={})


@pytest.mark.parametrize(
    "toml",
    [
        "[[zim]]\nname='a'\nflavour='b'\n",  # no [policy]
        "[policy]\nmin_age_months=6\nkeep_versions=3\n",  # no [[zim]]
        "[policy]\nmin_age_months=6\nkeep_versions=0\n[[zim]]\nname='a'\nflavour='b'\n",
        "[policy]\nmin_age_months=6\nkeep_versions=3\n[[zim]]\nname='a'\n",  # no flavour
        "[policy]\nmin_age_months=6\nkeep_versions=3\n[[zim]]\nname='a'\nflavour='b'\n",  # no [kiwix_tools]
    ],
)
def test_invalid_config_toml(tmp_path, toml):
    config_path, env_path = write(tmp_path, toml=toml)
    with pytest.raises(ConfigError):
        load_config(config_path, env_path, environ={})


def test_repo_files_agree_with_defaults():
    """The committed config.toml loads, and .env.example sets the same values as
    ENV_DEFAULTS (they're meant to stay in step)."""
    load_config(REPO_ROOT / "config.toml", REPO_ROOT / "missing.env", environ={})
    example = parse_env_file((REPO_ROOT / ".env.example").read_text())
    assert example == ENV_DEFAULTS
