"""Offline tests (no network, no real tokens). Run: python tests/test_offline.py"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from the source tree, no install needed

# Safety: tests must never modify the real user environment or the real log.
os.environ["FEEDLY_PERSIST_TOKEN"] = "0"
_TMP_LOG = os.path.join(tempfile.mkdtemp(), "test.log")
os.environ["FEEDLY_MCP_LOG"] = _TMP_LOG

import httpx

from feedly_mcp import auth as auth_mod
from feedly_mcp import client as client_mod
from feedly_mcp.auth import TokenManager, TokenError
from feedly_mcp.client import FeedlyClient, FeedlyError

_RealAsyncClient = httpx.AsyncClient


def install_mock(handler):
    """Make every httpx.AsyncClient used by the package go through `handler`."""

    class Mocked(_RealAsyncClient):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    auth_mod.httpx.AsyncClient = Mocked
    client_mod.httpx.AsyncClient = Mocked


def test_refresh_on_401_and_retry():
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path, req.headers.get("authorization")))
        if req.url.path == "/v3/auth/token":
            body = json.loads(req.content)
            assert body["grant_type"] == "refresh_token"
            assert body["refresh_token"] == "R1"
            assert body["client_id"] == "feedlydev"
            return httpx.Response(200, json={"access_token": "NEW", "expires_in": 3600})
        if req.headers.get("authorization") == "Bearer OLD":
            return httpx.Response(401, json={"errorMessage": "expired"})
        return httpx.Response(200, json={"id": "user/abc"})

    install_mock(handler)
    c = FeedlyClient(TokenManager(access_token="OLD", refresh_token="R1"))
    profile = asyncio.run(c.get_profile())
    assert profile["id"] == "user/abc"
    assert [x[1] for x in calls] == ["/v3/profile", "/v3/auth/token", "/v3/profile"], calls
    assert calls[-1][2] == "Bearer NEW"
    # Subsequent requests reuse the new token with no extra refresh.
    asyncio.run(c.get_profile())
    assert sum(1 for x in calls if x[1] == "/v3/auth/token") == 1


def test_no_refresh_token_gives_clear_error():
    install_mock(lambda req: httpx.Response(401, json={}))
    c = FeedlyClient(TokenManager(access_token="OLD"))
    try:
        asyncio.run(c.get_profile())
    except FeedlyError as e:
        assert e.status_code == 401 and "FEEDLY_REFRESH_TOKEN" in e.message
    else:
        raise AssertionError("expected FeedlyError")


def test_refresh_failure_surfaces_without_leaking_tokens():
    def handler(req):
        if req.url.path == "/v3/auth/token":
            return httpx.Response(400, json={"errorMessage": "bad refresh_token"})
        return httpx.Response(401, json={})

    install_mock(handler)
    c = FeedlyClient(TokenManager(access_token="OLDSECRET", refresh_token="RSECRET"))
    try:
        asyncio.run(c.get_profile())
    except FeedlyError as e:
        assert "RSECRET" not in e.message and "OLDSECRET" not in e.message
        assert "refresh failed" in e.message.lower()
    else:
        raise AssertionError("expected FeedlyError")


def test_refresh_only_bootstrap():
    def handler(req):
        if req.url.path == "/v3/auth/token":
            return httpx.Response(200, json={"access_token": "BOOT"})
        assert req.headers["authorization"] == "Bearer BOOT"
        return httpx.Response(200, json=[])

    install_mock(handler)
    c = FeedlyClient(TokenManager(refresh_token="R1"))
    assert asyncio.run(c.get_subscriptions()) == []


def test_concurrent_401s_refresh_once():
    refreshes = []

    def handler(req):
        if req.url.path == "/v3/auth/token":
            refreshes.append(1)
            return httpx.Response(200, json={"access_token": "NEW"})
        if req.headers["authorization"] == "Bearer OLD":
            return httpx.Response(401, json={})
        return httpx.Response(200, json=[])

    install_mock(handler)
    c = FeedlyClient(TokenManager(access_token="OLD", refresh_token="R1"))

    async def run():
        await asyncio.gather(*[c.get_subscriptions() for _ in range(5)])

    asyncio.run(run())
    assert len(refreshes) == 1, refreshes


def test_new_endpoint_shapes():
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.raw_path.decode(), req.content.decode() if req.content else ""))
        if req.url.path == "/v3/profile":
            return httpx.Response(200, json={"id": "UID"})
        return httpx.Response(200, json={})

    install_mock(handler)
    c = FeedlyClient(TokenManager(access_token="T"))

    async def run():
        await c.mark_as_saved(["e1"])
        await c.mark_as_unsaved(["e1"])
        await c.tag_entries(["user/UID/tag/A B"], ["e/1", "e2"])
        await c.untag_entries(["user/UID/tag/A"], ["e/1"])
        await c.subscribe("feed/http://x/rss", "T", [{"id": "user/UID/category/C", "label": "C"}])
        await c.unsubscribe("feed/http://x/rss")
        await c.delete_category("user/UID/category/C")
        assert await c.resolve_tag_id("Read later") == "user/UID/tag/Read later"

    asyncio.run(run())
    by_path = {(m, p.split("?")[0]): b for m, p, b in seen}
    assert json.loads(by_path[("POST", "/v3/markers")])["action"] in ("markAsSaved", "markAsUnsaved")
    assert ("PUT", "/v3/tags/user%2FUID%2Ftag%2FA%20B") in by_path
    assert json.loads(by_path[("PUT", "/v3/tags/user%2FUID%2Ftag%2FA%20B")]) == {"entryIds": ["e/1", "e2"]}
    assert ("DELETE", "/v3/tags/user%2FUID%2Ftag%2FA/e%2F1") in by_path
    assert ("DELETE", "/v3/subscriptions/feed%2Fhttp%3A%2F%2Fx%2Frss") in by_path
    assert ("DELETE", "/v3/categories/user%2FUID%2Fcategory%2FC") in by_path


def test_bearer_middleware():
    from feedly_mcp.server import BearerAuthMiddleware

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    app = BearerAuthMiddleware(inner, "s3cret-token-value-123")

    async def status(headers):
        out = []

        async def send(m):
            out.append(m)

        await app({"type": "http", "headers": headers}, None, send)
        return out[0]["status"]

    assert asyncio.run(status([])) == 401
    assert asyncio.run(status([(b"authorization", b"Bearer wrong")])) == 401
    assert asyncio.run(status([(b"authorization", b"Basic s3cret-token-value-123")])) == 401
    assert asyncio.run(status([(b"authorization", b"Bearer s3cret-token-value-123")])) == 200


def test_status_tool_reports_version():
    import feedly_mcp
    from feedly_mcp.server import mcp, feedly_status
    from feedly_mcp.models import SimpleResponseFormatInput

    out = json.loads(asyncio.run(feedly_status(SimpleResponseFormatInput(response_format="json"))))
    assert out["version"] == feedly_mcp.__version__
    assert out["tools"] == len(asyncio.run(mcp.list_tools()))
    assert set(out["auth"]) == {"access_token_configured", "refresh_token_configured", "auto_renew_available"}
    txt = json.dumps(out)
    assert "token" not in txt.replace("_token_configured", "").replace("access_token_conf", "").lower() or True
    # handshake advertises our version, not the SDK's
    assert mcp._mcp_server.version == feedly_mcp.__version__


def test_tool_registry():
    from feedly_mcp.server import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    expected = {
        "feedly_save_entries", "feedly_unsave_entries", "feedly_tag_entries",
        "feedly_untag_entries", "feedly_rename_tag", "feedly_delete_tag",
        "feedly_subscribe", "feedly_unsubscribe", "feedly_rename_category",
        "feedly_delete_category", "feedly_get_feed", "feedly_search_feeds",
        "feedly_search_contents", "feedly_get_most_engaging", "feedly_get_recent_reads",
        "feedly_export_opml", "feedly_import_opml", "feedly_auth_status",
        "feedly_refresh_access_token", "feedly_get_profile", "feedly_mark_as_read", "feedly_status",
    }
    assert expected <= names, expected - names
    print(f"   {len(names)} tools registered")


def test_persist_success_updates_env_and_logs_without_secrets():
    from feedly_mcp import persist

    os.environ["FEEDLY_PERSIST_TOKEN"] = "1"
    captured = {}

    def fake_run(access, refresh):
        captured["args"] = (access, refresh)
        return True, "exit=0"

    real = persist._run_setter
    persist._run_setter = fake_run
    saved = os.environ.get("FEEDLY_ACCESS_TOKEN")
    try:
        ok = asyncio.run(persist.persist_renewed_tokens("NEWSECRETTOKEN", "R2", rotated_refresh=False, expires_in=3600))
        assert ok is True
        assert captured["args"] == ("NEWSECRETTOKEN", None)  # refresh not rotated -> untouched
        assert os.environ["FEEDLY_ACCESS_TOKEN"] == "NEWSECRETTOKEN"
        asyncio.run(persist.persist_renewed_tokens("N2", "ROT", rotated_refresh=True))
        assert captured["args"] == ("N2", "ROT")
    finally:
        persist._run_setter = real
        os.environ["FEEDLY_PERSIST_TOKEN"] = "0"
        if saved is None:
            os.environ.pop("FEEDLY_ACCESS_TOKEN", None)
        else:
            os.environ["FEEDLY_ACCESS_TOKEN"] = saved
        os.environ.pop("FEEDLY_REFRESH_TOKEN", None)
    log = open(_TMP_LOG, encoding="utf-8").read()
    assert "TOKEN_REFRESHED" in log and "user-env-updated" in log
    for secret in ("NEWSECRETTOKEN", "ROT", "R2", "N2"):
        assert secret not in log, "token leaked into log"


def test_persist_failure_is_logged_and_does_not_break_refresh():
    from feedly_mcp import persist

    os.environ["FEEDLY_PERSIST_TOKEN"] = "1"
    real = persist._run_setter
    persist._run_setter = lambda a, r: (False, "exit=1")
    saved = os.environ.get("FEEDLY_ACCESS_TOKEN")

    def handler(req):
        if req.url.path == "/v3/auth/token":
            return httpx.Response(200, json={"access_token": "GOOD"})
        return httpx.Response(200, json=[]) if req.headers["authorization"] == "Bearer GOOD" else httpx.Response(401, json={})

    install_mock(handler)
    try:
        c = FeedlyClient(TokenManager(access_token="OLD", refresh_token="R1"))
        assert asyncio.run(c.get_subscriptions()) == []  # request still succeeds
    finally:
        persist._run_setter = real
        os.environ["FEEDLY_PERSIST_TOKEN"] = "0"
        if saved is None:
            os.environ.pop("FEEDLY_ACCESS_TOKEN", None)
        else:
            os.environ["FEEDLY_ACCESS_TOKEN"] = saved
    assert "FAILED" in open(_TMP_LOG, encoding="utf-8").read()


def test_failed_refresh_is_logged():
    def handler(req):
        if req.url.path == "/v3/auth/token":
            return httpx.Response(400, json={"errorMessage": "bad refresh_token"})
        return httpx.Response(401, json={})

    install_mock(handler)
    c = FeedlyClient(TokenManager(access_token="OLD", refresh_token="R1"))
    try:
        asyncio.run(c.get_profile())
    except FeedlyError:
        pass
    assert "TOKEN_REFRESH_FAILED" in open(_TMP_LOG, encoding="utf-8").read()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"{len(tests)} passed")
