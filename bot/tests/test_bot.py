"""Tests for the Discord client that need no Discord connection.

Constructing the client and building the command tree exercises the
app_commands decorators, which is where a typo or a bad signature actually
bites -- and where it would otherwise only show up at deploy time, after a
Railway restart loop.

Nothing here connects, logs in, or needs a token.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

discord = pytest.importorskip("discord")

from northernsteppes_bot.bot import (  # noqa: E402
    NorthernSteppesBot,
    build_tree,
    current_year,
)
from northernsteppes_bot.config import DEFAULT_GUILD_ID, Config  # noqa: E402
from northernsteppes_bot.roster import MemberDirectory  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBERS_DIR = REPO_ROOT / "content" / "members"

EXPECTED_COMMANDS = {"rank", "gaps", "roster", "me"}


@pytest.fixture
def bot(monkeypatch) -> NorthernSteppesBot:
    for name in ("DISCORD_TOKEN", "LEADERSHIP_ROLE_ID", "SYNC_ENABLED", "DRY_RUN"):
        monkeypatch.delenv(name, raising=False)
    directory = MemberDirectory(MEMBERS_DIR)
    directory.load()
    client = NorthernSteppesBot(Config.from_env(), directory)
    build_tree(client)
    return client


def test_command_tree_builds(bot):
    assert {c.name for c in bot.tree.get_commands()} == EXPECTED_COMMANDS


def test_no_write_commands_are_registered(bot):
    """This pass is read-only. A write command appearing here without
    LEADERSHIP_ROLE_ID configured would be a serious regression."""
    names = {c.name for c in bot.tree.get_commands()}
    forbidden = {"dues", "award", "waiver", "veteran-garb", "member-add",
                 "link", "sync-now"}
    assert not (names & forbidden)


def test_every_command_is_described(bot):
    """Discord shows the description in the picker; a blank one is a bug."""
    for command in bot.tree.get_commands():
        assert command.description.strip()


def test_no_privileged_intents_requested(bot):
    """Slash commands need neither, and requesting them would force an
    approval step on the Discord application."""
    assert bot.intents.message_content is False
    assert bot.intents.members is False
    assert bot.intents.presences is False


def test_guild_defaults_to_northern_steppes(bot):
    assert bot.config.guild_id == DEFAULT_GUILD_ID


def test_write_commands_disabled_without_a_role(bot):
    assert bot.config.write_commands_enabled is False


def test_current_year_is_sane():
    assert 2020 < current_year() < 2100


# --- .env loading ----------------------------------------------------------

def test_env_file_path_sits_next_to_the_example():
    """bot/.env.example tells you to copy it to bot/.env; that has to be the
    file the bot actually reads."""
    from northernsteppes_bot.__main__ import ENV_FILE
    assert ENV_FILE.name == ".env"
    assert (ENV_FILE.parent / ".env.example").is_file()


# tempfile rather than pytest's tmp_path, for the same reason as in
# test_roster.py: tmp_path fails with a PermissionError when a stale
# pytest-of-<user> directory is left behind, which has nothing to do with
# what these check.

def test_load_env_file_is_a_noop_when_absent(monkeypatch):
    import northernsteppes_bot.__main__ as entry
    with tempfile.TemporaryDirectory() as raw:
        monkeypatch.setattr(entry, "ENV_FILE", Path(raw) / "nope.env")
        assert entry.load_env_file() is False


def test_env_file_does_not_override_real_environment(monkeypatch):
    """Railway injects real variables. A stale .env on someone's machine must
    never win over them."""
    import northernsteppes_bot.__main__ as entry
    with tempfile.TemporaryDirectory() as raw:
        env = Path(raw) / ".env"
        env.write_text("DISCORD_GUILD_ID=111\n", encoding="utf-8")
        monkeypatch.setattr(entry, "ENV_FILE", env)
        monkeypatch.setenv("DISCORD_GUILD_ID", "999")

        assert entry.load_env_file() is True
        assert Config.from_env().guild_id == 999


def test_env_file_fills_in_what_the_environment_lacks(monkeypatch):
    import northernsteppes_bot.__main__ as entry
    with tempfile.TemporaryDirectory() as raw:
        env = Path(raw) / ".env"
        env.write_text("DISCORD_GUILD_ID=4242\n", encoding="utf-8")
        monkeypatch.setattr(entry, "ENV_FILE", env)
        monkeypatch.delenv("DISCORD_GUILD_ID", raising=False)

        assert entry.load_env_file() is True
        assert Config.from_env().guild_id == 4242


# --- background member refresh ---------------------------------------------

def test_bot_starts_a_refresher_and_cancels_it(bot):
    """The refresher must be cancelled on close, or the task leaks and keeps
    reading files after the client has shut down."""
    import asyncio

    async def run():
        bot._refresher = asyncio.create_task(asyncio.sleep(3600))
        assert not bot._refresher.done()
        bot._refresher.cancel()
        try:
            await bot._refresher
        except asyncio.CancelledError:
            pass
        assert bot._refresher.cancelled()

    asyncio.run(run())


def test_refresh_survives_a_read_failure(bot, monkeypatch):
    """A transient failure must not kill the refresher and freeze the roster
    for the lifetime of the process."""
    import asyncio

    calls = []

    def boom():
        calls.append(1)
        raise OSError("disk hiccup")

    monkeypatch.setattr(bot.directory, "load", boom)
    bot.refresh_interval_seconds = 0.001

    async def run():
        task = asyncio.create_task(bot._refresh_members())
        await asyncio.sleep(0.05)
        still_running = not task.done()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return still_running

    still_running = asyncio.run(run())
    # Both halves matter: the load must actually have been attempted, and the
    # loop must have survived it.
    assert calls, "refresher never called load(); the test proved nothing"
    assert still_running, "an exception killed the refresh loop"


def test_refresh_interval_is_half_the_ttl(bot):
    assert bot.refresh_interval_seconds <= bot.directory.ttl_seconds
