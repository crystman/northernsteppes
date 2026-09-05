"""Tests for configuration, focused on the fail-closed properties.

These matter more than they look: a misread here is the difference between
"only leadership can record dues" and "anyone can".
"""

from __future__ import annotations

import pytest

from northernsteppes_bot.config import DEFAULT_GUILD_ID, Config, is_leadership

WRITE_VARS = (
    "DISCORD_TOKEN", "DISCORD_GUILD_ID", "LEADERSHIP_ROLE_ID",
    "DATABASE_URL", "SYNC_ENABLED", "DRY_RUN", "SYNC_DEBOUNCE_SECONDS",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in WRITE_VARS:
        monkeypatch.delenv(name, raising=False)


def test_empty_environment_disables_write_commands():
    assert Config.from_env().write_commands_enabled is False


def test_empty_environment_does_not_write_to_git():
    assert Config.from_env().may_write_to_git is False


def test_sync_defaults_off_and_dry_run_defaults_on():
    cfg = Config.from_env()
    assert cfg.sync_enabled is False
    assert cfg.dry_run is True


def test_role_id_and_database_enable_write_commands(monkeypatch):
    monkeypatch.setenv("LEADERSHIP_ROLE_ID", "12345")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
    assert Config.from_env().write_commands_enabled is True


def test_malformed_role_id_fails_closed(monkeypatch):
    """A typo must not become 'no restriction'."""
    monkeypatch.setenv("LEADERSHIP_ROLE_ID", "not-a-number")
    cfg = Config.from_env()
    assert cfg.leadership_role_id is None
    assert cfg.write_commands_enabled is False


def test_blank_role_id_fails_closed(monkeypatch):
    monkeypatch.setenv("LEADERSHIP_ROLE_ID", "   ")
    assert Config.from_env().write_commands_enabled is False


def test_guild_id_defaults_to_northern_steppes():
    assert Config.from_env().guild_id == DEFAULT_GUILD_ID


def test_guild_id_override(monkeypatch):
    monkeypatch.setenv("DISCORD_GUILD_ID", "999")
    assert Config.from_env().guild_id == 999


def test_sync_enabled_still_dry_runs_by_default(monkeypatch):
    """Turning sync on is not enough; DRY_RUN must also be turned off."""
    monkeypatch.setenv("SYNC_ENABLED", "true")
    cfg = Config.from_env()
    assert cfg.sync_enabled is True
    assert cfg.may_write_to_git is False


def test_live_writes_require_a_destination_too(monkeypatch):
    """Turning the switches on is not enough without somewhere to write: a
    sync with no repository or token would silently do nothing."""
    monkeypatch.setenv("SYNC_ENABLED", "true")
    monkeypatch.setenv("DRY_RUN", "false")
    assert Config.from_env().may_write_to_git is False

    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("SYNC_REPO", "o/r")
    assert Config.from_env().may_write_to_git is True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_truthy_flag_spellings(monkeypatch, value):
    monkeypatch.setenv("SYNC_ENABLED", value)
    assert Config.from_env().sync_enabled is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "banana"])
def test_non_truthy_flag_spellings(monkeypatch, value):
    monkeypatch.setenv("SYNC_ENABLED", value)
    assert Config.from_env().sync_enabled is False


# --- the actual permission gate -------------------------------------------

def test_is_leadership_false_when_role_unset():
    """The current deployment state: role not yet created."""
    cfg = Config.from_env()
    assert is_leadership(cfg, [111, 222]) is False


def test_is_leadership_false_for_unrelated_roles(monkeypatch):
    monkeypatch.setenv("LEADERSHIP_ROLE_ID", "555")
    assert is_leadership(Config.from_env(), [111, 222]) is False


def test_is_leadership_true_with_the_role(monkeypatch):
    monkeypatch.setenv("LEADERSHIP_ROLE_ID", "555")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
    assert is_leadership(Config.from_env(), [111, 555]) is True


def test_is_leadership_accepts_string_role_ids(monkeypatch):
    """Discord IDs are frequently strings; a type mismatch must not silently
    fail open or closed by accident."""
    monkeypatch.setenv("LEADERSHIP_ROLE_ID", "555")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
    assert is_leadership(Config.from_env(), ["555"]) is True


def test_is_leadership_false_for_empty_roles(monkeypatch):
    monkeypatch.setenv("LEADERSHIP_ROLE_ID", "555")
    assert is_leadership(Config.from_env(), []) is False


# --- writes need somewhere to go, too --------------------------------------

def test_role_without_a_database_still_disables_writes(monkeypatch):
    """Accepting a /dues command with no database would discard it silently,
    leaving leadership believing dues were recorded."""
    monkeypatch.setenv("LEADERSHIP_ROLE_ID", "555")
    cfg = Config.from_env()
    assert cfg.database_url is None
    assert cfg.write_commands_enabled is False


def test_database_without_a_role_disables_writes(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
    assert Config.from_env().write_commands_enabled is False


def test_both_together_enable_writes(monkeypatch):
    monkeypatch.setenv("LEADERSHIP_ROLE_ID", "555")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
    assert Config.from_env().write_commands_enabled is True


def test_posture_names_the_missing_piece(monkeypatch):
    assert "no leadership role" in Config.from_env().describe_posture()
    monkeypatch.setenv("LEADERSHIP_ROLE_ID", "555")
    assert "no database" in Config.from_env().describe_posture()


# --- token shape -----------------------------------------------------------

def test_a_bot_token_has_three_parts():
    from northernsteppes_bot.config import looks_like_a_bot_token
    assert looks_like_a_bot_token("MTU0NTYy.Gx3f2K.abc123") is True


def test_a_client_secret_is_rejected():
    """The likely mistake: it sits one tab away in the developer portal and
    Discord only answers with a bare 401 at login."""
    from northernsteppes_bot.config import looks_like_a_bot_token
    assert looks_like_a_bot_token("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6") is False


def test_an_empty_segment_is_rejected():
    from northernsteppes_bot.config import looks_like_a_bot_token
    assert looks_like_a_bot_token("MTU0NTYy..abc123") is False


def test_too_many_parts_is_rejected():
    from northernsteppes_bot.config import looks_like_a_bot_token
    assert looks_like_a_bot_token("a.b.c.d") is False


# --- the defaulted guild ---------------------------------------------------

def test_an_unset_guild_is_flagged_as_defaulted(monkeypatch):
    """The fallback is the real Northern Steppes guild, so a deployment that
    forgot to set it would register commands in production."""
    monkeypatch.delenv("DISCORD_GUILD_ID", raising=False)
    cfg = Config.from_env()
    assert cfg.guild_id == DEFAULT_GUILD_ID
    assert cfg.guild_is_defaulted is True


def test_an_explicit_guild_is_not_flagged(monkeypatch):
    monkeypatch.setenv("DISCORD_GUILD_ID", "1279582837749842092")
    cfg = Config.from_env()
    assert cfg.guild_is_defaulted is False


# --- the sync's three switches ---------------------------------------------

SYNC_VARS = ("GITHUB_TOKEN", "SYNC_REPO", "SYNC_ENABLED", "DRY_RUN")


def _sync_env(monkeypatch, **overrides):
    for name in SYNC_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)
    return Config.from_env()


def test_a_fresh_deployment_cannot_write_to_git(monkeypatch):
    assert _sync_env(monkeypatch).may_write_to_git is False


def test_enabling_sync_is_not_enough(monkeypatch):
    """DRY_RUN still defaults on."""
    cfg = _sync_env(monkeypatch, SYNC_ENABLED="true",
                    GITHUB_TOKEN="x", SYNC_REPO="o/r")
    assert cfg.may_write_to_git is False


def test_clearing_dry_run_is_not_enough(monkeypatch):
    """Without a token and repo there is nowhere to write."""
    cfg = _sync_env(monkeypatch, SYNC_ENABLED="true", DRY_RUN="false")
    assert cfg.sync_configured is False
    assert cfg.may_write_to_git is False


def test_a_token_without_a_repo_is_not_configured(monkeypatch):
    cfg = _sync_env(monkeypatch, SYNC_ENABLED="true", DRY_RUN="false",
                    GITHUB_TOKEN="x")
    assert cfg.may_write_to_git is False


def test_all_three_together_allow_writing(monkeypatch):
    cfg = _sync_env(monkeypatch, SYNC_ENABLED="true", DRY_RUN="false",
                    GITHUB_TOKEN="x", SYNC_REPO="o/r")
    assert cfg.may_write_to_git is True


def test_sync_branch_defaults_to_main(monkeypatch):
    assert _sync_env(monkeypatch).sync_branch == "main"
