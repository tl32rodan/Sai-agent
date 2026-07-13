from __future__ import annotations

from pathlib import Path

from sai.config import (
    DEFAULT_CONFIG_TOML, Config, config_path, load_config, parse_config, state_dir,
)


class TestParseConfig:
    def test_shipped_default_toml_equals_code_defaults(self):
        # Appendix B and the Config dataclass must never drift apart.
        assert parse_config(DEFAULT_CONFIG_TOML) == Config()

    def test_empty_toml_gives_defaults(self):
        assert parse_config("") == Config()

    def test_overrides(self):
        config = parse_config(
            '[endpoint]\nurl = "http://localhost:1234"\nmodel = "llama3"\n'
            "[policy]\ncooldown_min = 5\n[keys]\npull = \"y\"\n"
        )
        assert config.url == "http://localhost:1234"
        assert config.model == "llama3"
        assert config.cooldown_min == 5.0
        assert config.pull_key == "y"
        assert config.bucket_capacity == 3  # untouched sections keep defaults


class TestPaths:
    def test_state_dir_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SAI_STATE_DIR", str(tmp_path / "s"))
        assert state_dir() == tmp_path / "s"

    def test_state_dir_xdg(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SAI_STATE_DIR", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        assert state_dir() == tmp_path / "sai"

    def test_config_path_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SAI_CONFIG", str(tmp_path / "c.toml"))
        assert config_path() == tmp_path / "c.toml"

    def test_load_config_missing_file_gives_defaults(self, tmp_path):
        assert load_config(tmp_path / "nope.toml") == Config()

    def test_load_config_reads_file(self, tmp_path):
        path = tmp_path / "c.toml"
        path.write_text('[endpoint]\nmodel = "phi"\n')
        assert load_config(path).model == "phi"

    def test_default_paths_are_under_home(self, monkeypatch):
        for var in ("SAI_STATE_DIR", "SAI_CONFIG", "XDG_STATE_HOME", "XDG_CONFIG_HOME"):
            monkeypatch.delenv(var, raising=False)
        assert state_dir() == Path.home() / ".local" / "state" / "sai"
        assert config_path() == Path.home() / ".config" / "sai" / "config.toml"
