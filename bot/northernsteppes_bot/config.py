"""Configuration, loaded from the environment.

On Railway these arrive as service variables. Locally they come from bot/.env,
which is gitignored -- see bot/.env.example.

The important property here is that everything dangerous **fails closed**. A
missing or malformed setting disables the capability it guards rather than
falling back to a permissive default, so a half-configured deployment can read
but never write.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: Discord guild (server) the bot registers its commands in.
DEFAULT_GUILD_ID = 183746241098678273

#: Repository member files are read from when they are not on disk.
DEFAULT_MEMBERS_REPO = "jackhumbert/northernsteppes"


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_or_none(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        # A malformed ID must not silently become "no restriction".
        return None


def looks_like_a_bot_token(token: str) -> bool:
    """Cheap shape check for a Discord bot token.

    A bot token is three dot-separated parts; an OAuth2 client secret is a
    single opaque string. Confusing the two is easy -- they sit one tab apart
    in the developer portal -- and the only feedback Discord gives is a 401
    at login, which reads as a crash rather than a configuration mistake.
    """
    return token.count(".") == 2 and all(part for part in token.split("."))


@dataclass(frozen=True)
class Config:
    discord_token: str | None
    guild_id: int
    leadership_role_id: int | None
    leadership_role_name: str | None
    database_url: str | None
    sync_enabled: bool
    dry_run: bool
    sync_debounce_seconds: int
    _guild_was_defaulted: bool
    members_dir: str | None
    members_repo: str
    members_ref: str

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            discord_token=os.environ.get("DISCORD_TOKEN") or None,
            guild_id=_int_or_none("DISCORD_GUILD_ID") or DEFAULT_GUILD_ID,
            _guild_was_defaulted=_int_or_none("DISCORD_GUILD_ID") is None,
            leadership_role_id=_int_or_none("LEADERSHIP_ROLE_ID"),
            leadership_role_name=(
                os.environ.get("LEADERSHIP_ROLE_NAME", "").strip() or None
            ),
            database_url=os.environ.get("DATABASE_URL") or None,
            # Both default off: a fresh deployment reads but does not write
            # until someone deliberately turns writing on.
            sync_enabled=_flag("SYNC_ENABLED", False),
            dry_run=_flag("DRY_RUN", True),
            sync_debounce_seconds=_int_or_none("SYNC_DEBOUNCE_SECONDS") or 300,
            members_dir=os.environ.get("MEMBERS_DIR", "").strip() or None,
            members_repo=(
                os.environ.get("MEMBERS_REPO", "").strip() or DEFAULT_MEMBERS_REPO
            ),
            members_ref=os.environ.get("MEMBERS_REF", "").strip() or "main",
        )

    @property
    def guild_is_defaulted(self) -> bool:
        """True when DISCORD_GUILD_ID was not set.

        Worth surfacing: the fallback is the real Northern Steppes guild, so
        an unconfigured test deployment would otherwise quietly point at
        production.
        """
        return self._guild_was_defaulted

    @property
    def write_commands_enabled(self) -> bool:
        """Whether commands that modify member records may run at all.

        Requires both a leadership role and a database.

        Without the role there is no way to tell leadership from anyone else,
        and the safe reading of "no role configured" is "nobody is
        leadership", not "everybody is".

        A configured LEADERSHIP_ROLE_NAME does not by itself enable writes:
        the name has to resolve to exactly one role on the guild first, which
        happens at startup and replaces this config with the resolved id.

        Without a database there is nowhere for a write to go. Accepting the
        command and discarding it would be worse than refusing: leadership
        would believe dues were recorded when nothing was.
        """
        return self.leadership_role_id is not None and self.database_url is not None

    @property
    def may_write_to_git(self) -> bool:
        """Whether the sync job may actually push commits."""
        return self.sync_enabled and not self.dry_run

    def describe_posture(self) -> str:
        """One-line summary for the startup log, so the running mode is
        obvious in Railway's logs rather than inferred."""
        if self.write_commands_enabled:
            writes = "enabled"
        elif self.leadership_role_id is None:
            writes = (
                f"DISABLED (role {self.leadership_role_name!r} not resolved yet)"
                if self.leadership_role_name
                else "DISABLED (no leadership role)"
            )
        else:
            writes = "DISABLED (no database)"
        bits = [
            f"guild={self.guild_id}",
            "write-commands=" + writes,
            "git-sync="
            + (
                "live"
                if self.may_write_to_git
                else ("dry-run" if self.sync_enabled else "off")
            ),
        ]
        return " ".join(bits)


def is_leadership(config: Config, role_ids) -> bool:
    """Whether a member holding ``role_ids`` may run write commands.

    Called inside every write handler. Discord's ``default_member_permissions``
    only hides a command in the picker; it does not stop anyone who knows the
    command name, so this is the actual gate.
    """
    if not config.write_commands_enabled:
        return False
    return config.leadership_role_id in {int(r) for r in role_ids}
