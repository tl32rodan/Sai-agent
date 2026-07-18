from __future__ import annotations

from pathlib import Path

from sai.config import (
    DEFAULT_CONFIG_TOML, Config, config_path, load_config, parse_config,
    runtime_dir, short_hostname, state_dir, state_root,
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
        assert config.bucket_capacity == 10  # untouched sections keep defaults


class TestPaths:
    def test_state_dir_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SAI_STATE_DIR", str(tmp_path / "s"))
        assert state_dir() == tmp_path / "s"

    def test_state_dir_xdg_is_host_scoped(self, monkeypatch, tmp_path):
        monkeypatch.delenv("SAI_STATE_DIR", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        assert state_dir() == tmp_path / "sai" / short_hostname()
        assert state_root() == tmp_path / "sai"

    def test_state_root_is_parent_of_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SAI_STATE_DIR", str(tmp_path / "root" / "hostA"))
        assert state_root() == tmp_path / "root"

    def test_short_hostname_has_no_dots(self):
        assert "." not in short_hostname() and short_hostname()

    def test_runtime_dir_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SAI_RUNTIME_DIR", str(tmp_path / "rt"))
        assert runtime_dir() == tmp_path / "rt"

    def test_runtime_dir_ignores_xdg_and_is_per_user_tmp(self, monkeypatch):
        # deliberately independent of XDG_RUNTIME_DIR: the user's shells and
        # the tmux server may disagree about it (some setups unset it)
        monkeypatch.delenv("SAI_RUNTIME_DIR", raising=False)
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/99999")
        import os
        assert runtime_dir() == Path(f"/tmp/sai-{os.getuid()}")

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
        assert state_dir() == Path.home() / ".local" / "state" / "sai" / short_hostname()
        assert config_path() == Path.home() / ".config" / "sai" / "config.toml"
