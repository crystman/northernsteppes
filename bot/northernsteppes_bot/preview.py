"""Preview what the website will look like once the sync runs.

    python -m northernsteppes_bot.preview            # report, change nothing
    python -m northernsteppes_bot.preview --write    # apply, then build the site

The website is static: Zola builds it from `content/members/*.md`, and it never
reads the database. So a change recorded in Discord is invisible on the site
until the sync commits the rendered files. Until that sync is configured, this
closes the loop by hand -- same database, same renderer, same output, just
written to the working tree instead of committed.

`--write` edits `content/members/` in place. `git diff` shows exactly what a
sync would have committed, and `git checkout -- content/members/` undoes it.

Reads `DATABASE_URL`. On Railway, `railway run python -m
northernsteppes_bot.preview` injects the deployed service's variables into a
local run, so the database URL never has to be copied anywhere.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import logging
import sys
from pathlib import Path

from . import db
from .config import Config
from .renderer import render_changes
from .roster import MemberDirectory, default_members_dir
from .sources import choose_source
from .store import MemberStore

log = logging.getLogger("northernsteppes_bot.preview")


def diff_for(slug: str, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(True), after.splitlines(True),
        fromfile=f"a/content/members/_{slug}.md",
        tofile=f"b/content/members/_{slug}.md",
    ))


async def render_from_database(members_dir: Path) -> tuple[dict[str, str], dict]:
    """Return (changed files by slug, the current file contents)."""
    config = Config.from_env()
    if not config.database_url:
        raise SystemExit(
            "DATABASE_URL is not set. Point it at a database, or run this "
            "under `railway run` to borrow the deployed service's variables."
        )

    source = choose_source(members_dir, config.members_repo, config.members_ref)
    directory = MemberDirectory(members_dir, source=source)
    sheets = directory.load()

    pool = await db.connect(config.database_url)
    try:
        store = MemberStore(pool)
        overlaid = await store.overlay(sheets)
    finally:
        await pool.close()

    files = {
        path.stem.lstrip("_"): path.read_text(encoding="utf-8")
        for path in sorted(members_dir.glob("_*.md"))
        if path.name != "_index.md"
    }
    return render_changes(files, {s.slug: s for s in overlaid}), files


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--write", action="store_true",
        help="apply the changes to content/members/ instead of only reporting",
    )
    parser.add_argument(
        "--members-dir", type=Path, default=None,
        help="defaults to the repository's content/members",
    )
    args = parser.parse_args(argv)

    members_dir = args.members_dir or default_members_dir()
    if not members_dir.is_dir():
        raise SystemExit(f"no member files at {members_dir}")

    changed, files = asyncio.run(render_from_database(members_dir))

    if not changed:
        print("Nothing to apply: the member files already match the database.")
        return 0

    for slug in sorted(changed):
        print(diff_for(slug, files[slug], changed[slug]), end="")

    print(f"\n{len(changed)} file(s) differ from the database.")
    if not args.write:
        print("Re-run with --write to apply them, then `zola serve` to look.")
        return 0

    for slug, text in sorted(changed.items()):
        (members_dir / f"_{slug}.md").write_text(text, encoding="utf-8")
    print(
        f"Wrote {len(changed)} file(s) to {members_dir}.\n"
        "Build with `zola serve`, and undo with "
        "`git checkout -- content/members/`."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
