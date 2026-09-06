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


#: Origins allowed to read the API when API_ALLOWED_ORIGINS is unset.
DEFAULT_API_ORIGINS = (
    "https://northernsteppes.com",
    "https://www.northernsteppes.com",
    "http://127.0.0.1:1111",   # zola serve
    "http://localhost:1111",
)


def _origins(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return DEFAULT_API_ORIGINS
    return tuple(part.strip() for part in raw.split(",") if part.strip())


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
    _guild_was_defaulted: bool
    api_port: int | None
    api_allowed_origins: tuple[str, ...]

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
            # Railway sets PORT for a service with a public domain. Without
            # one the bot stays a private worker and serves nothing.
            api_port=_int_or_none("PORT"),
            # Comma-separated. A fork deployed to its own Pages URL needs its
            # origin added, which should not require a code change.
            api_allowed_origins=_origins("API_ALLOWED_ORIGINS"),
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
        return f"guild={self.guild_id} write-commands={writes}"


def is_leadership(config: Config, role_ids) -> bool:
    """Whether a member holding ``role_ids`` may run write commands.

    Called inside every write handler. Discord's ``default_member_permissions``
    only hides a command in the picker; it does not stop anyone who knows the
    command name, so this is the actual gate.
    """
    if not config.write_commands_enabled:
        return False
    return config.leadership_role_id in {int(r) for r in role_ids}
