"""Tests for the read-only API.

Two things get the most attention. First, that nothing private leaks: the API
is unauthenticated, so anything it returns is public, and `discord_user_id` in
particular must never appear. Second, that it stays read-only -- no route may
change anything.

Uses aiohttp's test client, so requests go through the real routing and
middleware rather than calling handlers directly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

aiohttp = pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from northernsteppes_bot.api import (  # noqa: E402
    ALLOWED_ORIGINS,
    build_app,
    member_json,
)
from northernsteppes_bot.members import load_all  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBERS_DIR = REPO_ROOT / "content" / "members"


@pytest.fixture
def sheets():
    return load_all(MEMBERS_DIR)


@pytest.fixture
async def client(sheets):
    async def provider():
        return sheets

    app = build_app(provider, lambda: 2026)
    async with TestClient(TestServer(app)) as client:
        yield client


# --- what it serves --------------------------------------------------------

async def test_health_answers(client):
    response = await client.get("/api/health")
    assert response.status == 200
    assert (await response.json())["status"] == "ok"


async def test_members_lists_everyone(client, sheets):
    response = await client.get("/api/members")
    body = await response.json()
    assert len(body["members"]) == len(sheets)
    assert body["year"] == 2026


async def test_a_single_member_can_be_fetched(client):
    body = await (await client.get("/api/members/lamp")).json()
    assert body["slug"] == "lamp"
    assert body["name"] == "Lamp"
    assert body["rank"] == "Harbinger"


async def test_member_lookup_is_case_insensitive(client):
    assert (await client.get("/api/members/LAMP")).status == 200


async def test_an_unknown_member_is_a_404(client):
    response = await client.get("/api/members/nobody")
    assert response.status == 404
    assert (await response.json())["error"] == "no such member"


async def test_dues_state_is_included(client):
    """The whole point: the page corrects its stale dues line from this."""
    body = await (await client.get("/api/members/lamp")).json()
    assert body["dues"]["state"] in {"paid", "behind", "never"}
    assert body["dues"]["year"] == 2026


# --- what it must not serve ------------------------------------------------

def test_discord_id_is_never_in_the_payload(sheets):
    """It exists only because of the bot, and would map a Discord account to
    a member for anyone who asked."""
    payload = json.dumps([member_json(s, 2026) for s in sheets])
    assert "discord" not in payload.lower()


async def test_no_discord_id_over_the_wire(client):
    body = await (await client.get("/api/members")).text()
    assert "discord" not in body.lower()


def test_payload_holds_nothing_the_site_does_not_already_show(sheets):
    """Everything here is already on the member pages, so an unauthenticated
    API adds no exposure."""
    allowed = {
        "slug", "name", "rank", "dues", "waiver", "veteran_garb",
        "units", "weapons", "classes", "gaps",
    }
    assert set(member_json(sheets[0], 2026)) == allowed


# --- read only -------------------------------------------------------------

@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
async def test_writes_are_not_routed(client, method):
    response = await getattr(client, method)("/api/members/lamp")
    assert response.status in (404, 405), (
        f"{method.upper()} should not be routed at all"
    )


# --- cross-origin ----------------------------------------------------------

async def test_the_site_origin_is_allowed(client):
    response = await client.get(
        "/api/members", headers={"Origin": "https://northernsteppes.com"}
    )
    assert response.headers["Access-Control-Allow-Origin"] == (
        "https://northernsteppes.com"
    )


async def test_zola_serve_is_allowed_for_local_work(client):
    response = await client.get(
        "/api/members", headers={"Origin": "http://127.0.0.1:1111"}
    )
    assert response.headers["Access-Control-Allow-Origin"] == (
        "http://127.0.0.1:1111"
    )


async def test_an_unknown_origin_gets_no_allow_header(client):
    """Not a security control on a public API, but the allowed set should be
    explicit rather than a wildcard."""
    response = await client.get(
        "/api/members", headers={"Origin": "https://not-us.example"}
    )
    assert "Access-Control-Allow-Origin" not in response.headers


async def test_preflight_is_answered(client):
    response = await client.options(
        "/api/members", headers={"Origin": ALLOWED_ORIGINS[0]}
    )
    assert response.status == 204
    assert response.headers["Access-Control-Allow-Methods"] == "GET"


async def test_responses_are_cacheable(client):
    response = await client.get("/api/members")
    assert "max-age" in response.headers["Cache-Control"]


# --- freshness -------------------------------------------------------------

async def test_the_api_reflects_current_state_not_a_snapshot(sheets):
    """The provider is called per request, so a database write is visible
    without restarting the bot."""
    async def provider():
        return sheets

    app = build_app(provider, lambda: 2026)
    async with TestClient(TestServer(app)) as client:
        before = await (await client.get("/api/members/lamp")).json()
        assert before["dues"]["state"] != "paid"

        sheets[[s.slug for s in sheets].index("lamp")].dues_years[2026] = True

        after = await (await client.get("/api/members/lamp")).json()
        assert after["dues"]["state"] == "paid"
