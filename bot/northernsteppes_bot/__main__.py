"""Entry point: `python -m northernsteppes_bot`.

Refuses to start rather than starting wrong. A bot that connects with no token
check, or that quietly finds zero members, looks healthy in Railway's logs
while doing nothing useful.
"""

from __future__ import annotations

import logging
import sys

from .bot import NorthernSteppesBot, build_tree
from .config import Config
from .roster import MemberDirectory

log = logging.getLogger("northernsteppes_bot")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    config = Config.from_env()
    if not config.discord_token:
        log.error("DISCORD_TOKEN is not set; nothing to connect with.")
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
