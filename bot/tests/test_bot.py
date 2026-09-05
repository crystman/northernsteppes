"""Tests for the Discord client that need no Discord connection.

Constructing the client and building the command tree exercises the
app_commands decorators, which is where a typo or a bad signature actually
bites -- and where it would otherwise only show up at deploy time, after a
Railway restart loop.

Nothing here connects, logs in, or needs a token.
"""

from __future__ import annotations

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
