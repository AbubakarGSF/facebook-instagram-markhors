"""Supabase client fallback tests (no network, MockTransport)."""

import asyncio

import httpx

from src.database.supabase import SupabaseDB


def _db() -> SupabaseDB:
    return SupabaseDB("https://x.supabase.co", "test-key")


def test_upsert_row_survives_empty_json_patch_body():
    """200 + empty body par r.json() crash ho kar insert fallback abort na kare."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, text="")
        return httpx.Response(200, text="")

    async def run() -> bool:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ) as client:
            return await _db()._upsert_row(
                client,
                "fb_accounts",
                {"username": "u", "fb_id": "5", "cookies": {}},
            )

    assert asyncio.run(run()) is True


def test_upsert_row_patches_by_id_when_body_has_row():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url.params.get("fb_id"))))
        return httpx.Response(200, json=[{"id": 1}])

    async def run() -> bool:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ) as client:
            return await _db()._upsert_row(
                client,
                "fb_accounts",
                {"username": "u", "fb_id": "5", "cookies": {}},
            )

    assert asyncio.run(run()) is True
    assert calls[0] == ("PATCH", "eq.5")


def test_upsert_strips_missing_optional_columns_and_retries():
    """Purane schema (year/media_url column nahi) par upsert fail na ho."""
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        bodies.append(body)
        if "year" in body or "media_url" in body:
            return httpx.Response(
                400,
                json={
                    "code": "PGRST204",
                    "message": "Could not find the 'year' column of "
                    "'fb_accounts' in the schema cache",
                },
            )
        return httpx.Response(201, text="")

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), trust_env=False
        ) as client:
            return await _db().upsert(
                client,
                "fb_accounts",
                [{
                    "username": "u",
                    "fb_id": "5",
                    "cookies": {},
                    "year": "2015",
                    "media_url": "https://cdn.example.com/a.jpg",
                }],
            )

    ok, err = asyncio.run(run())
    assert ok == 1 and err is None
    assert len(bodies) == 2
    assert "year" not in bodies[-1] and "media_url" not in bodies[-1]
