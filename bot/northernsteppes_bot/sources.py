"""Where member files are read from.

Locally the repository is checked out and `content/members/` sits beside the
bot. On a host it may not: Railway builds from `bot/` and the rest of the
repository is not in the image, so a bot that only reads the filesystem exits
at startup with nothing to serve.

Rather than tie the deploy layout to the code, the source is pluggable. The
local directory is used when it exists; otherwise the files are fetched from
GitHub, which works from anywhere and needs no credentials because the
repository is public.

The fetching source is deliberately synchronous. Loading happens at startup and
on a background task, never on the request path, and callers that must not
block run it through `asyncio.to_thread`.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Protocol

log = logging.getLogger(__name__)

#: Where member files live inside the repository.
MEMBERS_PATH = "content/members"

DEFAULT_REPO = "jackhumbert/northernsteppes"
DEFAULT_REF = "main"

#: GitHub blocks requests without one.
USER_AGENT = "northernsteppes-bot"

REQUEST_TIMEOUT = 15


class MemberSource(Protocol):
    """Returns member files as {filename: text}."""

    def fetch(self) -> dict[str, str]: ...

    def describe(self) -> str: ...


class LocalSource:
    """Read member files from a directory on disk."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def fetch(self) -> dict[str, str]:
        return {
            path.name: path.read_text(encoding="utf-8")
            for path in sorted(self.directory.glob("_*.md"))
            if path.name != "_index.md"
        }

    def describe(self) -> str:
        return str(self.directory)


def _get(url: str, accept: str = "application/vnd.github+json") -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": accept}
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        return response.read()


class GitHubSource:
    """Read member files from a public GitHub repository.

    One API call lists the directory; the file bodies come from
    raw.githubusercontent.com, which is a CDN rather than the rate-limited
    API. Unauthenticated API calls are capped at 60 an hour per address, so
    listing once per refresh rather than once per file matters.
    """

    def __init__(self, repo: str = DEFAULT_REPO, ref: str = DEFAULT_REF,
                 getter: Callable[..., bytes] = _get) -> None:
        self.repo = repo
        self.ref = ref
        self._get = getter

    @property
    def listing_url(self) -> str:
        return (
            f"https://api.github.com/repos/{self.repo}/contents/"
            f"{MEMBERS_PATH}?ref={self.ref}"
        )

    def raw_url(self, filename: str) -> str:
        return (
            f"https://raw.githubusercontent.com/{self.repo}/{self.ref}/"
            f"{MEMBERS_PATH}/{filename}"
        )

    def fetch(self) -> dict[str, str]:
        entries = json.loads(self._get(self.listing_url))
        names = [
            e["name"] for e in entries
            if e.get("type") == "file"
            and e["name"].startswith("_")
            and e["name"].endswith(".md")
            and e["name"] != "_index.md"
        ]
        files: dict[str, str] = {}
        for name in sorted(names):
            files[name] = self._get(
                self.raw_url(name), accept="text/plain"
            ).decode("utf-8")
        return files

    def describe(self) -> str:
        return f"github:{self.repo}@{self.ref}/{MEMBERS_PATH}"


def choose_source(local_dir: Path | None, repo: str, ref: str) -> MemberSource:
    """Pick a source: an existing local directory, else GitHub.

    Preferring local means development and tests read the working tree, so an
    uncommitted change is visible immediately rather than being masked by
    whatever is on the branch.
    """
    if local_dir is not None and local_dir.is_dir():
        return LocalSource(local_dir)
    log.info(
        "no member files at %s; reading them from GitHub instead",
        local_dir,
    )
    return GitHubSource(repo, ref)
