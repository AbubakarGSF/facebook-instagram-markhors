"""Unit tests for bot helpers, cookie parsing and platform checks."""

import asyncio

import httpx
import pytest

import src.bot as b
from src.platforms.base import BasePlatform
from src.platforms.facebook import FacebookPlatform
from src.platforms.instagram import InstagramPlatform


# ------------------------------ helpers ------------------------------

def test_to_int_supports_suffixes_and_commas():
    assert b._to_int("1,234") == 1234
    assert b._to_int("1.2k") == 1200
    assert b._to_int("3M") == 3_000_000
    assert b._to_int("2b") == 2_000_000_000
    assert b._to_int("12abc") == 12
    assert b._to_int(None) == 0
    assert b._to_int("abc", default=7) == 7


def test_split_csv_strips_spaces():
    assert b._split_csv(["PK,", "US"]) == ["PK", "US"]
    assert b._split_csv(["2015, 2016"]) == ["2015", "2016"]
    assert b._split_csv([", ,"]) == []


def test_safe_text_keeps_ellipsis_inside_limit():
    out = b.safe_text("x" * 50, max_len=10)
    assert len(out) == 10
    assert out.endswith("...")


def test_compose_post_text_reserves_branding_footer():
    variant = "A" * 5000
    out = b.compose_post_text(variant, "#Tag", branding=True)
    assert len(out) <= 4096
    assert out.endswith(b.post_signature("#Tag"))
    # footer pura hona chahiye, truncate nahi
    assert "WhatsApp:" in out and "Telegram:" in out


def test_compose_post_text_branding_off():
    out = b.compose_post_text("hello", "#Tag", branding=False)
    assert out == "hello"


def test_resolve_platform_cookie_first():
    fb = {"c_user": "1", "xs": "x"}
    ig = {"sessionid": "s", "ds_user_id": "2"}
    assert b.resolve_platform("1.txt", fb)[0] == "facebook"
    assert b.resolve_platform("random.txt", ig)[0] == "instagram"
    assert b.resolve_platform("fb.txt", ig)[0] == "instagram"
    p, src, _ = b.resolve_platform("x.txt", ig, explicit="facebook")
    assert p is None and src == "mismatch"
    p, src, _ = b.resolve_platform("x.txt", {"c_user": "1", "sessionid": "s"})
    assert p is None and src == "cookie"
    p, src, _ = b.resolve_platform("x.txt", {"datr": "d"})
    assert p is None and src == "unknown"


# --------------------------- cookie parsing ---------------------------

def test_cookie_parser_rejects_metadata_urls_and_tokens():
    raw = (
        "Homepage: https://example.com/profile\n"
        "access_token=secret-graph-token\n"
        "2FA: true\n"
        "c_user=100\n"
        "xs=secret\n"
    )
    data, _ = BasePlatform().parse_cookie_file(raw)
    assert data == {"c_user": "100", "xs": "secret"}


def test_cookie_parser_netscape_and_httponly():
    raw = (
        "#HttpOnly_.facebook.com\tTRUE\t/\tTRUE\t1999999999\tc_user\t100\n"
        ".facebook.com\tTRUE\t/\tTRUE\t1999999999\txs\tsecret\n"
    )
    data, lines = BasePlatform().parse_cookie_file(raw)
    assert data == {"c_user": "100", "xs": "secret"}
    assert lines[0].startswith("#HttpOnly_")


def test_build_cookie_file_is_reparsable():
    bp = BasePlatform()
    data, lines = bp.parse_cookie_file("c_user=100\nxs=secret\n")
    out = b.build_cookie_file({"Username": "u", "Status": "LIVE"}, lines)
    again, _ = bp.parse_cookie_file(out)
    assert again == data


# --------------------------- platform checks --------------------------

def _client(handler):
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), trust_env=False
    )


def test_fb_403_is_unknown_not_dead():
    async def run():
        client = _client(lambda request: httpx.Response(403, text="blocked"))
        try:
            return await FacebookPlatform().live_check_impl(
                client, {"c_user": "1", "xs": "x"}
            )
        finally:
            await client.aclose()

    res = asyncio.run(run())
    assert res["Status"] == "UNKNOWN"


def test_fb_live_uses_cookie_id_when_html_has_none():
    async def run():
        client = _client(
            lambda request: httpx.Response(
                200, text="<html><title>Facebook</title>/logout.php</html>"
            )
        )
        try:
            return await FacebookPlatform().live_check_impl(
                client, {"c_user": "12345", "xs": "x"}
            )
        finally:
            await client.aclose()

    res = asyncio.run(run())
    assert res["Status"] == "LIVE"
    assert res["fb_id"] == "12345"


def test_ig_empty_200_is_unknown_not_dead():
    async def run():
        client = _client(lambda request: httpx.Response(200, json={}))
        try:
            return await InstagramPlatform().live_check_impl(
                client, {"sessionid": "s"}
            )
        finally:
            await client.aclose()

    res = asyncio.run(run())
    assert res["Status"] == "UNKNOWN"


def test_ig_explicit_login_required_is_dead():
    async def run():
        client = _client(
            lambda request: httpx.Response(200, json={"message": "login_required"})
        )
        try:
            return await InstagramPlatform().live_check_impl(
                client, {"sessionid": "s"}
            )
        finally:
            await client.aclose()

    res = asyncio.run(run())
    assert res["Status"] == "DEAD"


# ------------------------------ posting -------------------------------

def test_ig_post_waits_for_media_and_publishes():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "container-1"})
        if request.method == "GET" and request.url.path.endswith("/container-1"):
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if request.method == "POST" and request.url.path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "post-1"})
        return httpx.Response(404, json={"error": "unexpected"})

    async def run():
        client = _client(handler)
        try:
            return await InstagramPlatform().post(
                client,
                {"sessionid": "s"},
                "hello",
                media="https://cdn.example.com/a.jpg",
                access_token="token",
                target_id="1789",
            )
        finally:
            await client.aclose()

    ok, post_id, err = asyncio.run(run())
    assert (ok, post_id, err) == (True, "post-1", None)
    assert any("/media" in path for _, path in calls)
    assert any("media_publish" in path for _, path in calls)


def test_ig_post_without_media_is_unsupported():
    async def run():
        client = _client(lambda request: httpx.Response(200, json={}))
        try:
            return await InstagramPlatform().post(
                client, {"sessionid": "s"}, "hello",
                access_token="token", target_id="1789",
            )
        finally:
            await client.aclose()

    ok, _, err = asyncio.run(run())
    assert ok is False
    assert err.startswith("UNSUPPORTED")


# ------------------------- verify route logic -------------------------

class _FakeClient:
    def __init__(self, proxy=None):
        self.proxy = proxy

    async def aclose(self):
        pass


class _FakePlatform:
    NAME = "facebook"

    def __init__(self, proxy_status="DEAD", direct_status="UNKNOWN"):
        self.proxy_status = proxy_status
        self.direct_status = direct_status

    def http_client(self, proxy=None):
        return _FakeClient(proxy)

    async def live_check(self, client, cookies):
        status = self.proxy_status if client.proxy else self.direct_status
        return {"Status": status, "Platform": "facebook", "Reason": "fake"}


def test_proxy_dead_not_confirmed_by_direct_becomes_unknown():
    res = asyncio.run(
        b._verify_cookie(
            _FakePlatform("DEAD", "UNKNOWN"),
            {"c_user": "1", "xs": "x"},
            proxy="http://u:p@1.2.3.4:8080",
        )
    )
    assert res["Status"] == "UNKNOWN"
    assert "unconfirmed" in res["Reason"]


def test_direct_dead_is_returned():
    res = asyncio.run(
        b._verify_cookie(
            _FakePlatform("DEAD", "DEAD"),
            {"c_user": "1", "xs": "x"},
        )
    )
    assert res["Status"] == "DEAD"


def test_verify_blocks_platform_mismatch():
    res = asyncio.run(
        b._verify_cookie(InstagramPlatform(), {"c_user": "1", "xs": "x"})
    )
    assert res["Status"] == "UNKNOWN"
    assert "mismatch" in res["Reason"]
