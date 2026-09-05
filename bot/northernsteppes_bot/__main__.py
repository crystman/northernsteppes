"""Entry point: `python -m northernsteppes_bot`.

Refuses to start rather than starting wrong. A bot that connects with no token
check, or that quietly finds zero members, looks healthy in Railway's logs
while doing nothing useful.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import discord

from .bot import (
    NorthernSteppesBot,
    build_admin_tree,
    build_tree,
    build_write_tree,
)
from .config import Config, looks_like_a_bot_token
from .roster import MemberDirectory, default_members_dir
from .sources import choose_source

log = logging.getLogger("northernsteppes_bot")

#: bot/.env, alongside .env.example.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env_file() -> bool:
    """Load bot/.env if present. Returns whether anything was loaded.

    Real environment variables win, so a stale .env on a developer's machine
    can never override what Railway injects. Optional: production sets service
    variables directly and needs no file.
    """
    if not ENV_FILE.is_file():
        return False
    try:
        from dotenv import load_dotenv
    except ImportError:
        log.warning(
            "%s exists but python-dotenv is not installed; its values are "
            "being ignored. Install requirements.txt, or export the variables.",
            ENV_FILE,
        )
        return False
    load_dotenv(ENV_FILE, override=False)
    return True


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if load_env_file():
        log.info("loaded settings from %s", ENV_FILE)

    config = Config.from_env()

    # The fallback guild is the real Northern Steppes server. A deployment
    # that forgot to set DISCORD_GUILD_ID would otherwise quietly register
    # its commands in production.
    if config.guild_is_defaulted:
        log.warning(
            "DISCORD_GUILD_ID is not set; defaulting to the Northern Steppes "
            "guild %s. Set it explicitly for a test deployment.",
            config.guild_id,
        )

    if not config.discord_token:
        log.error(
            "DISCORD_TOKEN is not set; nothing to connect with. Set it in the "
            "environment, or copy bot/.env.example to bot/.env and fill it in."
        )
        return 1

    # Member files come from disk when the repository is checked out beside
    # the bot, and from GitHub when it is not -- a host may build only bot/,
    # leaving content/members out of the image entirely.
    local = Path(config.members_dir) if config.members_dir else default_members_dir()
    source = choose_source(local, config.members_repo, config.members_ref)
    log.info("member files: %s", source.describe())

    # The bot refreshes on a background task, so the request path never
    # pays for a reload.
    directory = MemberDirectory(auto_reload=False, source=source)

    try:
        sheets = directory.load()
    except Exception:
        log.exception("could not read member files from %s", source.describe())
        return 1

    if not sheets:
        # An empty roster is almost certainly a misconfiguration rather than a
        # club with no members, and every command would answer confidently
        # wrong.
        log.error("no member files found at %s", source.describe())
        return 1

    log.info("loaded %d members | %s", len(sheets), config.describe_posture())

    if not looks_like_a_bot_token(config.discord_token):
        # Fail here rather than at login: Discord answers a malformed token
        # with a bare 401, which under a restart policy becomes a loop of
        # stack traces that says nothing about the cause.
        log.error(
            "DISCORD_TOKEN does not look like a bot token. A bot token has "
            "three dot-separated parts and comes from the developer portal's "
            "Bot tab; the OAuth2 Client Secret is a single string and will "
            "not work."
        )
        return 1

    bot = NorthernSteppesBot(config, directory)
    build_tree(bot)
    build_write_tree(bot)
    build_admin_tree(bot)
    return asyncio.run(run(bot, config))


#: How long to hold the process open after Discord rate-limits the login.
#: Each restart is another login attempt, and enough of them in quick
#: succession earn a Cloudflare block on the whole address -- so the backoff
#: has to outlast the restart, not just the request.
RATE_LIMIT_BACKOFF_SECONDS = 300


async def run(bot: NorthernSteppesBot, config: Config) -> int:
    """Serve the API, then connect to Discord.

    In that order deliberately. The API needs nothing from Discord, so a
    Discord outage, a bad token or a rate limit should not take the website's
    live member data down with it.
    """
    await bot.start_api()
    try:
        await bot.start(config.discord_token)
    except discord.LoginFailure:
        # Nothing about a rejected token is retryable, and repeating the
        # traceback on every restart adds nothing.
        log.error(
            "Discord rejected the token. Reset it on the developer portal's "
            "Bot tab and update DISCORD_TOKEN."
        )
        return 1
    except discord.HTTPException as exc:
        if exc.status != 429:
            raise
        # Exiting immediately would have the host restart us straight into
        # another login attempt, which is what earns the block in the first
        # place. Wait it out with the API still serving.
        log.error(
            "Discord is rate limiting logins (429). This is usually the "
            "result of repeated restarts. Holding for %d seconds before "
            "exiting so a restart does not immediately retry; the read API "
            "keeps serving meanwhile.",
            RATE_LIMIT_BACKOFF_SECONDS,
        )
        await asyncio.sleep(RATE_LIMIT_BACKOFF_SECONDS)
        return 1
    finally:
        await bot.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
