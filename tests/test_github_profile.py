"""Private GitHub snapshots: reject public repositories and unsafe destinations."""
import asyncio
import base64
import json

import httpx
import pytest

from music_bot.github_profile import (
    MAX_CONTENT_BYTES, ProfileSyncError, sync_private_profile,
)


PROFILE = {
    "schema_version": 1,
    "scope": "complete_music_library",
    "tracks": [{"id": 1, "artist": "Portishead", "title": "Roads"}],
}
REPO = "pacman-codes/music-taste-private"
SHA = "a" * 40
COMMIT = "b" * 40


def test_refuses_public_repo_without_writing():
    seen = []

    def handler(request):
        seen.append(request.method)
        return httpx.Response(200, json={
            "private": False,
            "full_name": REPO,
            "archived": False,
        })

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            with pytest.raises(ProfileSyncError, match="приватность"):
                await sync_private_profile(PROFILE, repo=REPO, token="fake_token", client=client)
    asyncio.run(run())
    assert seen == ["GET"]


def test_verified_private_repo_creates_only_metadata_snapshot():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "api.github.com"
        assert request.headers["Authorization"] == "Bearer fake_token"
        if request.url.path == "/repos/" + REPO:
            return httpx.Response(200, json={
                "private": True, "full_name": REPO, "archived": False
            })
        if request.method == "GET":
            return httpx.Response(404)
        assert request.method == "PUT"
        body = json.loads(request.content)
        assert "sha" not in body
        decoded = base64.b64decode(body["content"])
        assert json.loads(decoded)["tracks"][0]["title"] == "Roads"
        return httpx.Response(201, json={"commit": {"sha": COMMIT}})

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            return await sync_private_profile(
                PROFILE, repo=REPO, token="fake_token", client=client,
            )
    result = asyncio.run(run())
    assert result["commit_sha"] == COMMIT
    assert result["path"] == "profiles/latest.json"
    assert [r.method for r in requests] == ["GET", "GET", "PUT"]


def test_existing_private_file_uses_previous_sha():
    def handler(request):
        if request.url.path == "/repos/" + REPO:
            return httpx.Response(200, json={
                "private": True, "full_name": REPO, "archived": False,
            })
        if request.method == "GET":
            return httpx.Response(200, json={"sha": SHA, "type": "file"})
        assert json.loads(request.content)["sha"] == SHA
        return httpx.Response(200, json={"commit": {"sha": COMMIT}})

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            return await sync_private_profile(
                PROFILE, repo=REPO, token="fake_token", client=client,
            )
    assert asyncio.run(run())["commit_sha"] == COMMIT


@pytest.mark.parametrize("bad", [
    "pacman-codes/music", "PACMAN-CODES/MUSIC", "http://bad/path",
    "../other", "missing_repo", "user/repo/extra",
])
def test_rejects_unapproved_repository_destinations_without_network(bad):
    async def run():
        with pytest.raises(ProfileSyncError):
            await sync_private_profile(PROFILE, repo=bad, token="fake_token")
    asyncio.run(run())


def test_requires_private_metadata_success_and_capped_payload():
    attempts = []
    def handler(request):
        attempts.append(request.method)
        return httpx.Response(403, json={"message": "Forbidden"})

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            with pytest.raises(ProfileSyncError):
                await sync_private_profile(
                    PROFILE, repo=REPO, token="fake_token", client=client,
                )
            with pytest.raises(ProfileSyncError):
                await sync_private_profile(
                    {"schema_version": 2, "scope": "complete_music_library"},
                    repo=REPO, token="fake_token", client=client,
                )
            with pytest.raises(ProfileSyncError, match="слишком большой"):
                await sync_private_profile(
                    {
                        "schema_version": 1, "scope": "complete_music_library",
                        "data": "x" * MAX_CONTENT_BYTES,
                    },
                    repo=REPO, token="fake_token", client=client,
                )
    asyncio.run(run())
    assert attempts == ["GET"]
