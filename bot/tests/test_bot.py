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


# --- write commands and the permission gate --------------------------------

class FakeRole:
    def __init__(self, rid): self.id = rid


class FakeUser:
    def __init__(self, rid=None, uid=1):
        self.roles = [FakeRole(rid)] if rid is not None else []
        self.id = uid


class FakeInteraction:
    def __init__(self, user): self.user = user


@pytest.fixture
def write_bot(monkeypatch):
    """A bot with the write commands registered but nothing configured."""
    for name in ("DISCORD_TOKEN", "LEADERSHIP_ROLE_ID", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    from northernsteppes_bot.bot import build_write_tree
    directory = MemberDirectory(MEMBERS_DIR)
    directory.load()
    client = NorthernSteppesBot(Config.from_env(), directory)
    build_tree(client)
    build_write_tree(client)
    return client


WRITE_COMMANDS = {"dues", "award", "waiver", "veteran-garb", "link"}


def test_write_commands_are_registered(write_bot):
    assert WRITE_COMMANDS <= {c.name for c in write_bot.tree.get_commands()}


def test_write_commands_are_visible_even_when_disabled(write_bot):
    """Registered so they explain themselves, rather than being mysteriously
    absent while the role is being created."""
    assert write_bot.config.write_commands_enabled is False
    assert WRITE_COMMANDS <= {c.name for c in write_bot.tree.get_commands()}


def test_gate_refuses_when_no_role_is_configured(write_bot):
    import asyncio
    msg = asyncio.run(write_bot._require_leadership(FakeInteraction(FakeUser(1))))
    assert msg is not None and "no leadership role" in msg


def test_gate_refuses_when_no_database(write_bot, monkeypatch):
    monkeypatch.setenv("LEADERSHIP_ROLE_ID", "555")
    write_bot.config = Config.from_env()
    import asyncio
    msg = asyncio.run(write_bot._require_leadership(FakeInteraction(FakeUser(555))))
    assert msg is not None and "no database" in msg


def test_gate_refuses_a_member_without_the_role(write_bot, monkeypatch):
    monkeypatch.setenv("LEADERSHIP_ROLE_ID", "555")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
    write_bot.config = Config.from_env()
    write_bot.store = object()   # configured, so the gate reaches the role check
    import asyncio
    msg = asyncio.run(write_bot._require_leadership(FakeInteraction(FakeUser(1))))
    assert msg is not None and "Only leadership" in msg


def test_gate_allows_a_member_with_the_role(write_bot, monkeypatch):
    monkeypatch.setenv("LEADERSHIP_ROLE_ID", "555")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
    write_bot.config = Config.from_env()
    write_bot.store = object()
    import asyncio
    assert asyncio.run(
        write_bot._require_leadership(FakeInteraction(FakeUser(555)))
    ) is None


def test_gate_refuses_a_user_with_no_roles_at_all(write_bot, monkeypatch):
    monkeypatch.setenv("LEADERSHIP_ROLE_ID", "555")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
    write_bot.config = Config.from_env()
    write_bot.store = object()
    import asyncio
    msg = asyncio.run(write_bot._require_leadership(FakeInteraction(FakeUser(None))))
    assert msg is not None


# --- resolving the leadership role by name ---------------------------------

class NamedRole:
    def __init__(self, rid, name): self.id, self.name = rid, name


def test_role_name_resolves_to_a_single_match(write_bot, monkeypatch):
    monkeypatch.setenv("LEADERSHIP_ROLE_NAME", "Leadership")
    write_bot.config = Config.from_env()
    roles = [NamedRole(1, "Member"), NamedRole(2, "Leadership"), NamedRole(3, "Bot")]
    assert write_bot.resolve_leadership_role(roles) == 2


def test_role_name_matching_ignores_case_and_padding(write_bot, monkeypatch):
    monkeypatch.setenv("LEADERSHIP_ROLE_NAME", "  leadership ")
    write_bot.config = Config.from_env()
    assert write_bot.resolve_leadership_role([NamedRole(7, "Leadership")]) == 7


def test_duplicate_role_names_refuse_to_resolve(write_bot, monkeypatch):
    """Discord permits duplicate role names. Guessing between them is how
    write access lands on the wrong role."""
    monkeypatch.setenv("LEADERSHIP_ROLE_NAME", "Leadership")
    write_bot.config = Config.from_env()
    roles = [NamedRole(2, "Leadership"), NamedRole(9, "leadership")]
    assert write_bot.resolve_leadership_role(roles) is None


def test_missing_role_name_does_not_resolve(write_bot, monkeypatch):
    monkeypatch.setenv("LEADERSHIP_ROLE_NAME", "Leadership")
    write_bot.config = Config.from_env()
    assert write_bot.resolve_leadership_role([NamedRole(1, "Member")]) is None


def test_explicit_id_wins_over_the_name(write_bot, monkeypatch):
    """An id is stable through renames, so it should never be second-guessed
    by a name lookup."""
    monkeypatch.setenv("LEADERSHIP_ROLE_ID", "42")
    monkeypatch.setenv("LEADERSHIP_ROLE_NAME", "Leadership")
    write_bot.config = Config.from_env()
    assert write_bot.resolve_leadership_role([NamedRole(2, "Leadership")]) == 42


def test_no_name_and_no_id_resolves_to_nothing(write_bot):
    assert write_bot.resolve_leadership_role([NamedRole(2, "Leadership")]) is None


def test_a_name_alone_does_not_enable_writes(write_bot, monkeypatch):
    """Until it resolves against a real guild, a name is just a string."""
    monkeypatch.setenv("LEADERSHIP_ROLE_NAME", "Leadership")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
    cfg = Config.from_env()
    assert cfg.write_commands_enabled is False
    assert "not resolved yet" in cfg.describe_posture()


def test_writes_enable_once_the_name_resolves(write_bot, monkeypatch):
    import dataclasses
    monkeypatch.setenv("LEADERSHIP_ROLE_NAME", "Leadership")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
    cfg = Config.from_env()
    resolved = dataclasses.replace(cfg, leadership_role_id=2)
    assert resolved.write_commands_enabled is True
