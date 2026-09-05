"""In-memory view of the member roster.

Read commands source everything from the member files rather than the
database. That is not a shortcut around the schema -- it is correct for this
stage. Nothing writes to the database yet except the bootstrap import, which
reads those same files, so the two cannot disagree. And the fields the rank
rules need most (professions, and the class counters behind the ladders) are
deliberately not in the database while that system is being reworked.

When the rework lands and writes start flowing through Postgres, this becomes
a database query and the file path disappears. Until then a single source is
simpler and cannot drift.
"""

from __future__ import annotations

import time
from pathlib import Path

from .members import load_all
from .ranks import MemberSheet

#: Rebuild the cache at most this often. The files only change when someone
#: commits, so this is about bounding staleness, not about load.
DEFAULT_TTL_SECONDS = 300


def default_members_dir() -> Path:
    """Locate content/members relative to this package.

    Works from a repo checkout, which is how the bot runs both locally and on
    Railway. MEMBERS_DIR overrides it if the deployment layout differs.
    """
    return Path(__file__).resolve().parents[2] / "content" / "members"


class MemberDirectory:
    """Loads member sheets and looks them up by slug, name or Discord id."""

    def __init__(self, members_dir: Path | None = None,
                 ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self.members_dir = members_dir or default_members_dir()
        self.ttl_seconds = ttl_seconds
        self._sheets: list[MemberSheet] = []
        self._loaded_at: float | None = None

    def load(self) -> list[MemberSheet]:
        """Force a reload."""
        self._sheets = load_all(self.members_dir)
        self._loaded_at = time.monotonic()
        return self._sheets

    def all(self) -> list[MemberSheet]:
        # >= rather than >, so ttl_seconds=0 means "always reload". With > it
        # meant "reload after strictly more than 0 seconds", which never fires
        # when two calls land inside one clock tick -- time.monotonic() has
        # ~15.6ms resolution on Windows.
        if self._loaded_at is None or (
            time.monotonic() - self._loaded_at >= self.ttl_seconds
        ):
            self.load()
        return self._sheets

    def by_slug(self, slug: str) -> MemberSheet | None:
        slug = slug.strip().lower()
        return next((s for s in self.all() if s.slug.lower() == slug), None)

    def search(self, query: str) -> list[MemberSheet]:
        """Find members by slug or display name.

        Exact matches win outright; otherwise every substring match is
        returned so the caller can say "did you mean" rather than guessing.
        Guessing is how somebody's dues get recorded against the wrong person.
        """
        q = query.strip().lower()
        if not q:
            return []

        exact = [
            s for s in self.all()
            if s.slug.lower() == q or s.display_name.lower() == q
        ]
        if exact:
            return exact
        return [
            s for s in self.all()
            if q in s.slug.lower() or q in s.display_name.lower()
        ]

    def choices(self, query: str, limit: int = 25) -> list[MemberSheet]:
        """Autocomplete candidates. Discord allows at most 25."""
        q = query.strip().lower()
        pool = self.all() if not q else [
            s for s in self.all()
            if q in s.slug.lower() or q in s.display_name.lower()
        ]
        return sorted(pool, key=lambda s: s.display_name or s.slug)[:limit]
