"""Discord client and the read-only commands.

Deliberately thin. Every command resolves a member, calls a pure function in
views.py, and replies. All the logic worth testing lives in ranks.py, roster.py
and views.py, none of which need a Discord connection.

Read-only by design at this stage: nothing here writes to the database, the
repository, or Discord itself beyond replying. There is no write command until
LEADERSHIP_ROLE_ID exists and the repo write path is agreed, and config.py
refuses to enable them before then.
"""

from __future__ import annotations

import datetime as dt
import logging

import discord
from discord import app_commands

from .config import Config
from .roster import MemberDirectory
from . import views

log = logging.getLogger(__name__)


def current_year() -> int:
    return dt.date.today().year


class NorthernSteppesBot(discord.Client):
    def __init__(self, config: Config, directory: MemberDirectory) -> None:
        # No privileged intents: slash commands do not need message content or
        # the members intent, so the bot needs no extra approval to run.
        super().__init__(intents=discord.Intents.default())
        self.config = config
        self.directory = directory
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        guild = discord.Object(id=self.config.guild_id)
        # Guild-scoped commands appear immediately; global ones take up to an
        # hour to propagate, which makes iterating painful.
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        log.info("commands synced to guild %s", self.config.guild_id)

    async def on_ready(self) -> None:
        log.info(
            "connected as %s | %s | %d members loaded",
            self.user, self.config.describe_posture(), len(self.directory.all()),
        )


def build_tree(bot: NorthernSteppesBot) -> None:
    """Register the read-only commands."""
    directory = bot.directory
    tree = bot.tree

    async def member_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=s.display_name or s.slug, value=s.slug)
            for s in directory.choices(current)
        ]

    def resolve(query: str):
        """Return (sheet, error_message). Never guesses between matches."""
        matches = directory.search(query)
        if not matches:
            return None, views.format_no_match(query)
        if len(matches) > 1:
            return None, views.format_ambiguous(query, matches)
        return matches[0], None

    @tree.command(name="rank", description="Show a member's rank and why")
    @app_commands.describe(member="Member name (leave blank for yourself)")
    @app_commands.autocomplete(member=member_autocomplete)
    async def rank_cmd(interaction: discord.Interaction, member: str | None = None):
        if member is None:
            return await interaction.response.send_message(
                views.format_unlinked(current_year()), ephemeral=True
            )
        sheet, error = resolve(member)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        await interaction.response.send_message(views.format_rank(sheet))

    @tree.command(name="gaps", description="What a member needs for the next rank")
    @app_commands.describe(member="Member name")
    @app_commands.autocomplete(member=member_autocomplete)
    async def gaps_cmd(interaction: discord.Interaction, member: str):
        sheet, error = resolve(member)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        await interaction.response.send_message(views.format_gaps(sheet))

    @tree.command(name="roster", description="Current members, grouped by rank")
    async def roster_cmd(interaction: discord.Interaction):
        await interaction.response.send_message(
            views.format_roster(directory.all(), current_year())
        )

    @tree.command(name="me", description="Show your own proficiency sheet")
    async def me_cmd(interaction: discord.Interaction):
        # Requires a member <-> Discord mapping, which needs /link, which is a
        # leadership-gated write command. Until then this explains itself
        # rather than guessing from nicknames -- matching on a Discord display
        # name would show somebody else's sheet to the wrong person.
        await interaction.response.send_message(
            views.format_unlinked(current_year()), ephemeral=True
        )
