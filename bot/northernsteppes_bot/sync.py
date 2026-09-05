"""Commit rendered member files back to the repository.

The last link in the chain: a Discord command writes to Postgres, the overlay
makes it visible to the bot immediately, and this turns it into a commit that
rebuilds the website.

Debounced rather than per-command. One commit per command would be one deploy
per command, and the publish workflow sets `cancel-in-progress: false`, so
recording dues for twenty-one members at a gathering would queue twenty-one
sequential deploys. Instead writes mark `sync_state` dirty, and a quiet period
turns everything that changed into a single reviewable commit.

Two switches guard it, both defaulting off:

    SYNC_ENABLED=false   nothing runs at all
    DRY_RUN=true         renders and logs the diff, commits nothing

so a fresh deployment reads and records but cannot touch the repository until
someone deliberately turns both.

Writes go through the Git Data API rather than one file update per call, so a
batch of changes is one commit rather than one per member.
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from .ranks import MemberSheet
from .renderer import render_changes

log = logging.getLogger(__name__)

MEMBERS_PATH = "content/members"
USER_AGENT = "northernsteppes-bot"
REQUEST_TIMEOUT = 30

#: Anything the sync writes must be under here. A bug that produced a path
#: outside it would be committing to parts of the site nobody asked it to
#: touch, so it is refused rather than trusted.
ALLOWED_PREFIX = MEMBERS_PATH + "/"


class SyncError(RuntimeError):
    pass


class UnsafePath(SyncError):
    def __init__(self, path: str) -> None:
        super().__init__(f"refusing to write outside {ALLOWED_PREFIX}: {path!r}")
        self.path = path


@dataclass
class SyncResult:
    changed: dict[str, str] = field(default_factory=dict)
    commit_sha: str | None = None
    dry_run: bool = False

    @property
    def wrote(self) -> bool:
        return self.commit_sha is not None

    def summary(self) -> str:
        if not self.changed:
            return "nothing to sync"
        names = ", ".join(sorted(self.changed))
        if self.dry_run:
            return f"dry run: would update {len(self.changed)} file(s): {names}"
        return f"committed {len(self.changed)} file(s): {names} ({self.commit_sha})"


class GitHubWriter:
    """Minimal Git Data API client: read a tree, write a commit.

    Only the calls this needs, so there is no dependency to audit and the
    request shapes are visible here rather than behind a library.
    """

    def __init__(self, repo: str, branch: str, token: str,
                 opener: Callable[..., bytes] | None = None) -> None:
        self.repo = repo
        self.branch = branch
        self.token = token
        self._open = opener or self._request

    def _request(self, url: str, method: str = "GET",
                 payload: dict | None = None) -> bytes:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            url, data=data, method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return response.read()

    def _api(self, path: str) -> str:
        return f"https://api.github.com/repos/{self.repo}{path}"

    def head_sha(self) -> str:
        ref = json.loads(self._open(self._api(f"/git/ref/heads/{self.branch}")))
        return ref["object"]["sha"]

    def read_members(self, ref: str) -> dict[str, str]:
        """Current member files at `ref`, keyed by slug."""
        entries = json.loads(
            self._open(self._api(f"/contents/{MEMBERS_PATH}?ref={ref}"))
        )
        files = {}
        for entry in entries:
            name = entry.get("name", "")
            if entry.get("type") != "file" or not name.startswith("_"):
                continue
            if not name.endswith(".md") or name == "_index.md":
                continue
            blob = json.loads(self._open(self._api(f"/git/blobs/{entry['sha']}")))
            files[name.removesuffix(".md").lstrip("_")] = base64.b64decode(
                blob["content"]
            ).decode("utf-8")
        return files

    def commit(self, base_sha: str, files: dict[str, str], message: str) -> str:
        """Write `files` as one commit on top of `base_sha`. Returns its sha."""
        base = json.loads(self._open(self._api(f"/git/commits/{base_sha}")))

        tree_entries = []
        for path, content in sorted(files.items()):
            if not path.startswith(ALLOWED_PREFIX) or ".." in path:
                raise UnsafePath(path)
            blob = json.loads(self._open(
                self._api("/git/blobs"), "POST",
                {"content": content, "encoding": "utf-8"},
            ))
            tree_entries.append({
                "path": path, "mode": "100644", "type": "blob",
                "sha": blob["sha"],
            })

        tree = json.loads(self._open(
            self._api("/git/trees"), "POST",
            {"base_tree": base["tree"]["sha"], "tree": tree_entries},
        ))
        commit = json.loads(self._open(
            self._api("/git/commits"), "POST",
            {"message": message, "tree": tree["sha"], "parents": [base_sha]},
        ))
        # Not forced: a rejected update means somebody else pushed, and the
        # caller re-reads rather than overwriting their work.
        self._open(
            self._api(f"/git/refs/heads/{self.branch}"), "PATCH",
            {"sha": commit["sha"], "force": False},
        )
        return commit["sha"]


def commit_message(changed: dict[str, str], sheets: dict[str, MemberSheet]) -> str:
    """A message naming who changed, so the history is readable."""
    slugs = sorted(changed)
    names = [sheets[s].display_name or s for s in slugs if s in sheets]
    headline = (
        f"chore(members): update {len(slugs)} member record"
        f"{'s' if len(slugs) != 1 else ''}"
    )
    body = "\n".join(f"- {n}" for n in names) or "\n".join(f"- {s}" for s in slugs)
    return f"{headline}\n\n{body}\n\nRecorded via Discord."


def sync_once(writer: GitHubWriter, sheets: list[MemberSheet],
              dry_run: bool = True) -> SyncResult:
    """Render current state over the repository and commit any differences."""
    head = writer.head_sha()
    existing = writer.read_members(head)
    by_slug = {s.slug: s for s in sheets}

    changed = render_changes(existing, by_slug)
    if not changed:
        return SyncResult(dry_run=dry_run)

    if dry_run:
        for slug in sorted(changed):
            log.info("dry run: would update %s/_%s.md", MEMBERS_PATH, slug)
        return SyncResult(changed=changed, dry_run=True)

    files = {f"{MEMBERS_PATH}/_{slug}.md": text for slug, text in changed.items()}
    sha = writer.commit(head, files, commit_message(changed, by_slug))
    return SyncResult(changed=changed, commit_sha=sha)
