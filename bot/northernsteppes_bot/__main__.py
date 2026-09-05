"""Entry point: `python -m northernsteppes_bot`.

Refuses to start rather than starting wrong. A bot that connects with no token
check, or that quietly finds zero members, looks healthy in Railway's logs
while doing nothing useful.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .bot import NorthernSteppesBot, build_tree
from .config import Config
from .roster import MemberDirectory

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
    if not config.discord_token:
        log.error(
            "DISCORD_TOKEN is not set; nothing to connect with. Set it in the "
            "environment, or copy bot/.env.example to bot/.env and fill it in."
        )
        return 1

    directory = MemberDirectory()
    if not directory.members_dir.is_dir():
        log.error(
            "member files not found at %s. Set MEMBERS_DIR if the deployment "
            "layout differs from a repo checkout.",
            directory.members_dir,
        )
        return 1

    sheets = directory.load()
    if not sheets:
        # An empty roster is almost certainly a wrong path rather than a club
        # with no members, and every command would answer confidently wrong.
        log.error("no member files found in %s", directory.members_dir)
        return 1

    log.info("loaded %d members | %s", len(sheets), config.describe_posture())

    bot = NorthernSteppesBot(config, directory)
    build_tree(bot)
    bot.run(config.discord_token, log_handler=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
