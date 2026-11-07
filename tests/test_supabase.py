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
