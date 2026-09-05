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


@dataclass(frozen=True)
class Config:
    discord_token: str | None
    guild_id: int
    leadership_role_id: int | None
    database_url: str | None
    sync_enabled: bool
    dry_run: bool
    sync_debounce_seconds: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            discord_token=os.environ.get("DISCORD_TOKEN") or None,
            guild_id=_int_or_none("DISCORD_GUILD_ID") or DEFAULT_GUILD_ID,
            leadership_role_id=_int_or_none("LEADERSHIP_ROLE_ID"),
            database_url=os.environ.get("DATABASE_URL") or None,
            # Both default off: a fresh deployment reads but does not write
            # until someone deliberately turns writing on.
            sync_enabled=_flag("SYNC_ENABLED", False),
            dry_run=_flag("DRY_RUN", True),
            sync_debounce_seconds=_int_or_none("SYNC_DEBOUNCE_SECONDS") or 300,
        )

    @property
    def write_commands_enabled(self) -> bool:
        """Whether commands that modify member records may run at all.

        Requires both a leadership role and a database.

        Without the role there is no way to tell leadership from anyone else,
        and the safe reading of "no role configured" is "nobody is
        leadership", not "everybody is".

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
            writes = "DISABLED (no leadership role)"
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
