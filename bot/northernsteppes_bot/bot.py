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

import asyncio
import dataclasses
import datetime as dt
import logging

import discord
from discord import app_commands

from .config import Config, is_leadership
from .ranks import LEVEL_NAMES
from .roster import MemberDirectory
from . import store as store_mod
from .store import MemberStore, UnknownMember, UnknownProficiency
from . import views

log = logging.getLogger(__name__)


def current_year() -> int:
    return dt.date.today().year


class NorthernSteppesBot(discord.Client):
    def __init__(self, config: Config, directory: MemberDirectory,
                 store: MemberStore | None = None) -> None:
        # No privileged intents: slash commands do not need message content or
        # the members intent, so the bot needs no extra approval to run.
        super().__init__(intents=discord.Intents.default())
        self.config = config
        self.directory = directory
        self.store = store
        self.tree = app_commands.CommandTree(self)
        # Refresh at half the cache TTL so the list is never stale when a
        # command reads it. An attribute rather than a computed local so tests
        # can drive the loop without waiting minutes.
        self.refresh_interval_seconds = max(directory.ttl_seconds // 2, 30)

    async def setup_hook(self) -> None:
        await self._connect_store()

        # Reload off the request path. Autocomplete fires on every keystroke,
        # and a cache reload landing on one stalls it while 21 files are
        # re-parsed -- about 34ms, which is enough to feel.
        self._refresher = asyncio.create_task(self._refresh_members())

        guild = discord.Object(id=self.config.guild_id)
        # Guild-scoped commands appear immediately; global ones take up to an
        # hour to propagate, which makes iterating painful.
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        log.info("commands synced to guild %s", self.config.guild_id)

    async def _connect_store(self) -> None:
        """Open the database, if one is configured.

        A failure here leaves the bot read-only rather than refusing to start:
        /rank and /roster work from the files alone, and answering those is
        better than being entirely absent while the database is down.
        """
        if self.store is not None or not self.config.database_url:
            return
        try:
            from . import db
            pool = await db.connect(self.config.database_url)
            await db.apply_migrations(pool)
            self.store = MemberStore(pool)
            log.info("database connected")
        except Exception:
            log.exception(
                "database unavailable; continuing read-only, write commands "
                "will refuse"
            )

    async def _refresh_members(self) -> None:
        """Keep the member cache warm, forever."""
        while True:
            await asyncio.sleep(self.refresh_interval_seconds)
            try:
                # to_thread because load() blocks. Reading 21 local files is
                # ~34ms, but fetching them from GitHub is seconds, and holding
                # the event loop that long stalls Discord's heartbeat and
                # drops the connection.
                await asyncio.to_thread(self.directory.load)
            except Exception:
                # A transient read failure must not kill the refresher and
                # leave the roster frozen for the process's lifetime.
                log.exception("member refresh failed; keeping the previous list")

    async def close(self) -> None:
        task = getattr(self, "_refresher", None)
        if task is not None:
            task.cancel()
        await super().close()

    def resolve_leadership_role(self, roles) -> int | None:
        """Match LEADERSHIP_ROLE_NAME against the guild's roles.

        Returns the id only on an unambiguous match. Discord allows several
        roles to share a name, so a duplicate is refused rather than guessed
        at -- guessing is how write access lands on the wrong role. An
        explicitly configured id always wins and skips this entirely.
        """
        if self.config.leadership_role_id is not None:
            return self.config.leadership_role_id
        name = self.config.leadership_role_name
        if not name:
            return None

        wanted = name.strip().casefold()
        matches = [r for r in roles if r.name.strip().casefold() == wanted]
        if len(matches) == 1:
            return matches[0].id
        if not matches:
            log.error(
                "no role named %r on guild %s; write commands stay disabled",
                name, self.config.guild_id,
            )
        else:
            log.error(
                "%d roles named %r on guild %s; refusing to guess, write "
                "commands stay disabled. Set LEADERSHIP_ROLE_ID instead.",
                len(matches), name, self.config.guild_id,
            )
        return None

    async def _apply_leadership_role(self) -> None:
        """Resolve the role name once the guild cache is populated.

        Runs in on_ready rather than setup_hook: the guild is not available
        until the client has connected.
        """
        if self.config.leadership_role_id is not None:
            return
        guild = self.get_guild(self.config.guild_id)
        if guild is None:
            log.error(
                "not a member of guild %s; cannot resolve the leadership role",
                self.config.guild_id,
            )
            return
        resolved = self.resolve_leadership_role(guild.roles)
        if resolved is not None:
            self.config = dataclasses.replace(
                self.config, leadership_role_id=resolved
            )
            log.info(
                "leadership role %r resolved to id %s -- set LEADERSHIP_ROLE_ID "
                "to that to skip this lookup",
                self.config.leadership_role_name, resolved,
            )

    def _log_guilds(self) -> None:
        """List every guild this token is in, and flag unexpected ones.

        A token invited to more than the configured server is worth knowing
        about: it is how a test instance ends up answering in the real one.
        """
        others = [g for g in self.guilds if g.id != self.config.guild_id]
        log.info(
            "in %d guild(s); configured for %s",
            len(self.guilds), self.config.guild_id,
        )
        if others:
            log.warning(
                "also in %s -- commands are not registered there and writes "
                "from there are refused, but this token is shared with them",
                ", ".join(f"{g.name} ({g.id})" for g in others),
            )

    async def on_ready(self) -> None:
        await self._apply_leadership_role()
        self._log_guilds()
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

    async def sheets() -> list:
        """Current member state: files, with database values layered on."""
        base = directory.all()
        if bot.store is None:
            return base
        try:
            return await bot.store.overlay(base)
        except Exception:
            # Answering from the files is far better than answering not at
            # all; the overlay only adds recency.
            log.exception("database overlay failed; using the files alone")
            return base

    async def resolve_async(query: str):
        """Resolve against overlaid state. Never guesses between matches."""
        q = query.strip().lower()
        pool = await sheets()
        exact = [s for s in pool
                 if s.slug.lower() == q or s.display_name.lower() == q]
        matches = exact or [
            s for s in pool
            if q in s.slug.lower() or q in s.display_name.lower()
        ]
        if not matches:
            return None, views.format_no_match(query)
        if len(matches) > 1:
            return None, views.format_ambiguous(query, matches)
        return matches[0], None

    async def _require_leadership(interaction) -> str | None:
        """Return an error message if this user may not write, else None.

        Checked inside the handler. default_member_permissions only hides a
        command in the picker; it does not stop anyone who knows its name.
        """
        # Writes only from the configured guild. A bot invited to more than
        # one server -- a test instance also added to the real one, say --
        # must not edit real records from the wrong place.
        if interaction.guild_id != bot.config.guild_id:
            log.warning(
                "refused a write from guild %s; configured for %s",
                interaction.guild_id, bot.config.guild_id,
            )
            return views.format_wrong_guild()
        if not bot.config.write_commands_enabled or bot.store is None:
            return views.format_writes_disabled(bot.config)
        roles = getattr(interaction.user, "roles", None) or []
        if not is_leadership(bot.config, [r.id for r in roles]):
            return views.format_not_leadership()
        return None

    async def own_sheet(interaction):
        """The caller's own member sheet, if their Discord is linked.

        Returns (sheet, error). Looks the link up rather than matching on a
        Discord display name: names are not unique and are freely editable,
        so matching on one could show somebody else's record.
        """
        if bot.store is None:
            return None, views.format_unlinked(current_year())
        try:
            slug = await bot.store.slug_for_discord(interaction.user.id)
        except Exception:
            log.exception("could not look up the Discord link")
            return None, views.format_unlinked(current_year())
        if slug is None:
            return None, views.format_unlinked(current_year())
        sheet = next((s for s in await sheets() if s.slug == slug), None)
        if sheet is None:
            # Linked to a member that no longer exists.
            return None, views.format_link_dangling(slug)
        return sheet, None

    # Shared with build_write_tree, which registers the gated commands.
    bot._own_sheet = own_sheet
    bot._resolve = resolve_async
    bot._require_leadership = _require_leadership

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
            sheet, error = await own_sheet(interaction)
            if error:
                return await interaction.response.send_message(
                    error, ephemeral=True
                )
            return await interaction.response.send_message(
                views.format_rank(sheet)
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
        sheet, error = await own_sheet(interaction)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        # Ephemeral: a member's own sheet is for them, not the channel.
        await interaction.response.send_message(
            views.format_sheet(sheet), ephemeral=True
        )


def build_write_tree(bot: "NorthernSteppesBot") -> None:
    """Register the leadership-gated write commands.

    Registered whatever the configuration, so they are visible and explain
    themselves rather than silently missing. Every one refuses at the top if
    writes are disabled or the caller is not leadership.
    """
    tree = bot.tree
    directory = bot.directory

    async def member_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=s.display_name or s.slug, value=s.slug)
            for s in directory.choices(current)
        ]

    async def style_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if bot.store is None:
            return []
        names = await bot.store.known_proficiencies("weapon")
        q = current.strip().lower()
        return [
            app_commands.Choice(name=n, value=n)
            for n in names if q in n.lower()
        ][:25]

    async def guard(interaction, query: str):
        """Permission check then member lookup. Returns (sheet, error)."""
        denied = await bot._require_leadership(interaction)
        if denied:
            return None, denied
        return await bot._resolve(query)

    @tree.command(name="dues", description="Record that a member has paid dues")
    @app_commands.describe(member="Member name", year="Defaults to this year")
    @app_commands.autocomplete(member=member_autocomplete)
    async def dues_cmd(interaction: discord.Interaction, member: str,
                       year: int | None = None):
        sheet, error = await guard(interaction, member)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        year = year or current_year()
        try:
            recorded = await bot.store.record_dues(
                sheet.slug, year, str(interaction.user.id)
            )
        except UnknownMember:
            return await interaction.response.send_message(
                views.format_no_match(member), ephemeral=True
            )
        bot.directory.load()
        await interaction.response.send_message(
            views.format_dues_recorded(
                sheet.display_name or sheet.slug, year, already=not recorded
            )
        )

    @tree.command(name="award", description="Set a member's weapon style level")
    @app_commands.describe(
        member="Member name", style="Weapon style",
        level="0 none, 1 proficient, 2 adept, 3 master",
    )
    @app_commands.autocomplete(member=member_autocomplete, style=style_autocomplete)
    async def award_cmd(interaction: discord.Interaction, member: str,
                        style: str, level: app_commands.Range[int, 0, 3]):
        sheet, error = await guard(interaction, member)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        try:
            previous = await bot.store.set_proficiency(
                sheet.slug, "weapon", style, level, str(interaction.user.id)
            )
        except UnknownProficiency:
            known = await bot.store.known_proficiencies("weapon")
            return await interaction.response.send_message(
                views.format_unknown_proficiency(style, known), ephemeral=True
            )
        bot.directory.load()
        await interaction.response.send_message(
            views.format_award(
                sheet.display_name or sheet.slug, style, previous, level
            )
        )

    @tree.command(name="waiver", description="Record whether a waiver is on file")
    @app_commands.describe(member="Member name", signed="Is the waiver on file?")
    @app_commands.autocomplete(member=member_autocomplete)
    async def waiver_cmd(interaction: discord.Interaction, member: str,
                         signed: bool):
        sheet, error = await guard(interaction, member)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        await bot.store.set_flag(
            sheet.slug, "waiver", signed, str(interaction.user.id)
        )
        await interaction.response.send_message(
            views.format_flag_set(
                sheet.display_name or sheet.slug, "waiver on file", signed
            )
        )

    @tree.command(name="veteran-garb",
                  description="Record whether a member owns veteran garb")
    @app_commands.describe(member="Member name", owns="Do they own it?")
    @app_commands.autocomplete(member=member_autocomplete)
    async def garb_cmd(interaction: discord.Interaction, member: str, owns: bool):
        sheet, error = await guard(interaction, member)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        await bot.store.set_flag(
            sheet.slug, "veteran_garb", owns, str(interaction.user.id)
        )
        await interaction.response.send_message(
            views.format_flag_set(
                sheet.display_name or sheet.slug, "veteran garb", owns
            )
        )

    @tree.command(name="link",
                  description="Link a Discord account to a member record")
    @app_commands.describe(member="Member name", account="Discord account")
    @app_commands.autocomplete(member=member_autocomplete)
    async def link_cmd(interaction: discord.Interaction, member: str,
                       account: discord.User):
        # Leadership-gated on purpose: self-service linking would let anyone
        # claim another member's sheet.
        sheet, error = await guard(interaction, member)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        taken_from = await bot.store.link_discord(sheet.slug, account.id)
        await interaction.response.send_message(
            views.format_linked(
                sheet.display_name or sheet.slug, account.mention, taken_from
            )
        )


def build_admin_tree(bot: "NorthernSteppesBot") -> None:
    """Commands that manage records rather than edit them."""
    tree = bot.tree

    @tree.command(name="member-add", description="Create a new member record")
    @app_commands.describe(
        name="Display name, as it should appear on the site",
        slug="Optional. Defaults to a slug derived from the name.",
    )
    async def member_add_cmd(interaction: discord.Interaction, name: str,
                             slug: str | None = None):
        denied = await bot._require_leadership(interaction)
        if denied:
            return await interaction.response.send_message(denied, ephemeral=True)
        try:
            created = await store_mod.create_member(
                bot.store, name, slug, str(interaction.user.id)
            )
        except store_mod.DuplicateMember as exc:
            return await interaction.response.send_message(
                views.format_duplicate_member(exc.slug), ephemeral=True
            )
        except store_mod.InvalidSlug as exc:
            return await interaction.response.send_message(
                views.format_invalid_slug(exc.slug), ephemeral=True
            )
        await interaction.response.send_message(
            views.format_member_added(name.strip(), created)
        )

    @tree.command(name="sync-status",
                  description="Show what is waiting to reach the website")
    async def sync_status_cmd(interaction: discord.Interaction):
        # Readable by anyone: it exposes no member data, and "did my dues
        # actually go through" is a fair question for the person who paid.
        if bot.store is None:
            return await interaction.response.send_message(
                views.format_writes_disabled(bot.config), ephemeral=True
            )
        status = await store_mod.sync_status(bot.store)
        await interaction.response.send_message(
            views.format_sync_status(status, may_write=bot.config.may_write_to_git)
        )
