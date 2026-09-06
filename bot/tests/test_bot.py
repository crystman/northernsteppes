"""Tests for the Discord client that need no Discord connection.

Constructing the client and building the command tree exercises the
app_commands decorators, which is where a typo or a bad signature actually
bites -- and where it would otherwise only show up at deploy time, after a
Railway restart loop.

Nothing here connects, logs in, or needs a token.
"""

from __future__ import annotations

import os
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

EXPECTED_COMMANDS = {"rank", "gaps", "roster", "me"}


@pytest.fixture
def bot(monkeypatch) -> NorthernSteppesBot:
    for name in ("DISCORD_TOKEN", "LEADERSHIP_ROLE_ID"):
        monkeypatch.delenv(name, raising=False)
    client = NorthernSteppesBot(Config.from_env())
    build_tree(client)
    return client


def test_command_tree_builds(bot):
    assert {c.name for c in bot.tree.get_commands()} == EXPECTED_COMMANDS


def test_no_write_commands_are_registered(bot):
    """This pass is read-only. A write command appearing here without
    LEADERSHIP_ROLE_ID configured would be a serious regression."""
    names = {c.name for c in bot.tree.get_commands()}
    forbidden = {"dues", "dues-remove", "award", "profession", "waiver",
                 "veteran-garb", "member-add", "link"}
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

        try:
            assert entry.load_env_file() is True
            assert Config.from_env().guild_id == 4242
        finally:
            # load_dotenv writes straight into os.environ, which monkeypatch
            # does not track. Without this the value leaks into every test
            # that runs afterwards -- it was making the guild-scoping tests
            # fail in a full run while passing in isolation.
            os.environ.pop("DISCORD_GUILD_ID", None)


# --- reading members --------------------------------------------------------

def test_a_bot_with_no_database_has_no_members(bot):
    """There is no file cache any more. Without a database the bot knows
    nothing, and must say so rather than inventing an empty club."""
    assert bot.store is None


def test_reads_go_to_the_database_every_time(bot, monkeypatch):
    """Nothing is cached, so a write in Discord shows up in the next read
    without waiting for anything to expire."""
    import asyncio

    calls = []

    class FakeStore:
        async def all_members(self):
            calls.append(1)
            return []

    bot.store = FakeStore()
    command = bot.tree.get_commands()

    async def run():
        # The roster command's own reader, reached the way the commands reach
        # it, so this cannot pass by testing a helper nothing calls.
        await bot._choices("")
        await bot._choices("")

    asyncio.run(run())
    assert command, "expected commands to be registered"
    assert len(calls) == 2, f"expected one read per call, got {len(calls)}"


# --- write commands and the permission gate --------------------------------

class FakeRole:
    def __init__(self, rid): self.id = rid


class FakeUser:
    def __init__(self, rid=None, uid=1):
        self.roles = [FakeRole(rid)] if rid is not None else []
        self.id = uid


class FakeInteraction:
    """Defaults to the configured guild, so tests that are about the role
    check are not accidentally testing the guild check."""

    def __init__(self, user, guild_id=DEFAULT_GUILD_ID):
        self.user = user
        self.guild_id = guild_id


@pytest.fixture
def write_bot(monkeypatch):
    """A bot with the write commands registered but nothing configured."""
    for name in ("DISCORD_TOKEN", "LEADERSHIP_ROLE_ID", "DATABASE_URL",
                 "DISCORD_GUILD_ID"):
        monkeypatch.delenv(name, raising=False)
    from northernsteppes_bot.bot import build_write_tree
    client = NorthernSteppesBot(Config.from_env())
    build_tree(client)
    build_write_tree(client)
    return client


WRITE_COMMANDS = {"dues", "dues-remove", "award", "profession", "waiver",
                  "veteran-garb", "link"}


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


# --- guild scoping ---------------------------------------------------------

class GuildInteraction:
    def __init__(self, guild_id, user): self.guild_id, self.user = guild_id, user


def _configured(write_bot, monkeypatch):
    monkeypatch.setenv("LEADERSHIP_ROLE_ID", "555")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
    write_bot.config = Config.from_env()
    write_bot.store = object()
    return write_bot


def test_writes_refused_from_another_guild(write_bot, monkeypatch):
    """A test instance invited to the real server must not edit real records."""
    import asyncio
    bot = _configured(write_bot, monkeypatch)
    other = GuildInteraction(999999999, FakeUser(555))
    msg = asyncio.run(bot._require_leadership(other))
    assert msg is not None and "different server" in msg


def test_writes_allowed_from_the_configured_guild(write_bot, monkeypatch):
    import asyncio
    bot = _configured(write_bot, monkeypatch)
    here = GuildInteraction(bot.config.guild_id, FakeUser(555))
    assert asyncio.run(bot._require_leadership(here)) is None


def test_guild_check_precedes_the_role_check(write_bot, monkeypatch):
    """Wrong server should say so, rather than leaking whether the caller
    would otherwise have had permission."""
    import asyncio
    bot = _configured(write_bot, monkeypatch)
    other = GuildInteraction(999999999, FakeUser(None))
    msg = asyncio.run(bot._require_leadership(other))
    assert "different server" in msg


# --- surviving a Discord outage or rate limit ------------------------------

def test_api_starts_before_connecting_to_discord(write_bot, monkeypatch):
    """The API needs nothing from Discord, so a bad token, an outage or a rate
    limit must not take the website's live member data down with it."""
    import asyncio
    import northernsteppes_bot.__main__ as entry

    order = []

    async def fake_start_api():
        order.append("api")

    async def fake_start(token):
        order.append("discord")
        raise discord.LoginFailure("nope")

    async def fake_close():
        order.append("close")

    monkeypatch.setattr(write_bot, "start_api", fake_start_api)
    monkeypatch.setattr(write_bot, "start", fake_start)
    monkeypatch.setattr(write_bot, "close", fake_close)
    monkeypatch.setenv("DISCORD_TOKEN", "a.b.c")

    code = asyncio.run(entry.run(write_bot, Config.from_env()))
    assert code == 1
    assert order[0] == "api", "the API must be up before Discord is attempted"


def test_a_rate_limit_backs_off_before_exiting(write_bot, monkeypatch):
    """Exiting immediately has the host restart straight into another login
    attempt, which is what earns the block."""
    import asyncio
    import northernsteppes_bot.__main__ as entry

    slept = []

    async def fake_start_api():
        pass

    async def fake_start(token):
        raise discord.HTTPException(
            type("R", (), {"status": 429, "reason": "Too Many Requests"})(),
            "rate limited",
        )

    async def fake_close():
        pass

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(write_bot, "start_api", fake_start_api)
    monkeypatch.setattr(write_bot, "start", fake_start)
    monkeypatch.setattr(write_bot, "close", fake_close)
    monkeypatch.setattr(entry.asyncio, "sleep", fake_sleep)
    monkeypatch.setenv("DISCORD_TOKEN", "a.b.c")

    code = asyncio.run(entry.run(write_bot, Config.from_env()))
    assert code == 1
    assert slept == [entry.RATE_LIMIT_BACKOFF_SECONDS]


def test_other_http_errors_are_not_swallowed(write_bot, monkeypatch):
    """Only a 429 gets the backoff; anything else should surface."""
    import asyncio
    import northernsteppes_bot.__main__ as entry

    async def noop(*a, **k):
        pass

    async def fake_start(token):
        raise discord.HTTPException(
            type("R", (), {"status": 500, "reason": "Server Error"})(),
            "boom",
        )

    monkeypatch.setattr(write_bot, "start_api", noop)
    monkeypatch.setattr(write_bot, "start", fake_start)
    monkeypatch.setattr(write_bot, "close", noop)
    monkeypatch.setenv("DISCORD_TOKEN", "a.b.c")

    with pytest.raises(discord.HTTPException):
        asyncio.run(entry.run(write_bot, Config.from_env()))


# --- the write commands, end to end -----------------------------------------

# Against a real database and through the registered callback, because the
# bugs that reached production were all in this layer rather than in the store:
# a command reading the wrong source, or never reaching the database at all.
# Calling store methods directly would have caught none of them.

import os  # noqa: E402

requires_db = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="set TEST_DATABASE_URL to run database tests",
)


class Reply:
    """Captures what the command sent back."""

    def __init__(self):
        self.messages = []

    async def send_message(self, content, ephemeral=False):
        self.messages.append(content)


class LiveInteraction:
    def __init__(self, guild_id, user_id=555, role=555):
        self.guild_id = guild_id
        self.user = FakeUser(role, user_id)
        self.response = Reply()

    @property
    def sent(self):
        return "\n".join(self.response.messages)


def command(bot, name):
    """The callback Discord would invoke for a slash command."""
    return next(c for c in bot.tree.get_commands() if c.name == name).callback


@pytest.fixture
async def live_bot(monkeypatch, sample_sheets):
    """A fully configured bot wired to a seeded database."""
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from conftest import seed

    from northernsteppes_bot import db
    from northernsteppes_bot.bot import build_admin_tree, build_write_tree
    from northernsteppes_bot.store import MemberStore

    monkeypatch.setenv("LEADERSHIP_ROLE_ID", "555")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
    monkeypatch.delenv("DISCORD_GUILD_ID", raising=False)

    pool = await db.connect(os.environ["TEST_DATABASE_URL"])
    async with pool.acquire() as conn:
        await conn.execute("drop schema public cascade; create schema public;")
    await db.apply_migrations(pool)
    await seed(pool, sample_sheets)

    client = NorthernSteppesBot(Config.from_env(), MemberStore(pool))
    build_tree(client)
    build_write_tree(client)
    build_admin_tree(client)
    yield client
    await pool.close()


@requires_db
async def test_profession_command_promotes_to_harbinger(live_bot):
    """The route the file deletion closed. savage has the four basic styles,
    so garb plus an Adept profession is the whole remaining gap -- and until
    now no command could award the profession."""
    from northernsteppes_bot import ranks

    await command(live_bot, "veteran-garb")(
        LiveInteraction(DEFAULT_GUILD_ID), "savage", True
    )

    interaction = LiveInteraction(DEFAULT_GUILD_ID)
    await command(live_bot, "profession")(interaction, "savage", "Cook", 2)
    assert "Adept" in interaction.sent

    sheets = {s.slug: s for s in await live_bot.store.all_members()}
    assert ranks.rank_name(sheets["savage"]) == "Harbinger"


@requires_db
async def test_profession_command_rejects_an_unknown_profession(live_bot):
    """And says what is allowed, rather than surfacing a database error."""
    interaction = LiveInteraction(DEFAULT_GUILD_ID)
    await command(live_bot, "profession")(interaction, "savage", "Beekeeper", 2)
    assert "Beekeeper" in interaction.sent
    assert "Cook" in interaction.sent, "expected the known names to be listed"


@requires_db
async def test_profession_command_is_refused_without_the_role(live_bot):
    interaction = LiveInteraction(DEFAULT_GUILD_ID, role=None)
    await command(live_bot, "profession")(interaction, "savage", "Cook", 2)
    assert "Only leadership" in interaction.sent

    sheets = {s.slug: s for s in await live_bot.store.all_members()}
    assert sheets["savage"].professions.get("Cook", 0) == 0, "the write went through"


@requires_db
async def test_profession_command_is_refused_from_another_guild(live_bot):
    """A test instance invited to the real server must not edit real records."""
    interaction = LiveInteraction(999999999)
    await command(live_bot, "profession")(interaction, "savage", "Cook", 2)
    assert "different server" in interaction.sent

    sheets = {s.slug: s for s in await live_bot.store.all_members()}
    assert sheets["savage"].professions.get("Cook", 0) == 0


@requires_db
async def test_dues_remove_command_undoes_a_dues_year(live_bot):
    from northernsteppes_bot import ranks

    interaction = LiveInteraction(DEFAULT_GUILD_ID)
    await command(live_bot, "dues-remove")(interaction, "savage", 2026)
    assert "2026" in interaction.sent

    sheets = {s.slug: s for s in await live_bot.store.all_members()}
    assert sheets["savage"].paid_for(2026) is False
    # It was their only year, so this is a demotion the reply must mention.
    assert ranks.rank_name(sheets["savage"]) == "Peasant"
    assert "Peasant" in interaction.sent


@requires_db
async def test_dues_remove_is_refused_without_the_role(live_bot):
    interaction = LiveInteraction(DEFAULT_GUILD_ID, role=None)
    await command(live_bot, "dues-remove")(interaction, "savage", 2026)
    assert "Only leadership" in interaction.sent

    sheets = {s.slug: s for s in await live_bot.store.all_members()}
    assert sheets["savage"].paid_for(2026) is True, "the delete went through"
