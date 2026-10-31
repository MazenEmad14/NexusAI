"""Cookie sessions: httpOnly cookies, refresh rotation, CSRF protection (plan 3.9). Runs the real routes against
a throwaway MongoDB database; skipped when none is reachable."""
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import auth_utils
from helpers.config import Settings, get_settings
from helpers.error_handlers import install_error_handlers
from models.SessionModel import SessionModel, hash_token
from models.indexes import ensure_all_indexes
from routes import local_auth

MONGO_URL = "mongodb://localhost:27017/"
STRONG = "correct horse 42"
BASE = "/api/v1/auth/local"
ORIGIN = get_settings().CORS_ORIGINS[0]


def _mongo_available() -> bool:
    import pymongo

    try:
        pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=1000).server_info()
        return True
    except Exception:
        return False


needs_mongo = pytest.mark.skipif(not _mongo_available(), reason="MongoDB is not reachable")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(local_auth, "_client_ip", lambda request: request.headers.get("x-test-ip", "10.0.0.1"))
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(local_auth.local_auth_router)
    db_name = f"test_sessions_{uuid.uuid4().hex[:8]}"

    @app.on_event("startup")
    async def start():
        conn = AsyncIOMotorClient(MONGO_URL)
        app.state.services = SimpleNamespace(mongo_conn=conn, mongo_db=conn[db_name])
        await ensure_all_indexes(app.state.services.mongo_db)

    @app.on_event("shutdown")
    async def stop():
        await app.state.services.mongo_conn.drop_database(db_name)
        app.state.services.mongo_conn.close()

    with TestClient(app, base_url="http://localhost") as test_client:
        test_client.db = lambda: app.state.services.mongo_db
        yield test_client


def _run(client, coro_fn):
    return client.portal.call(coro_fn)


def _signup(client, email="alice@example.com"):
    response = client.post(f"{BASE}/signup", json={"name": "Alice", "email": email, "password": STRONG})
    assert response.status_code == 200, response.text
    return response


def _put_cookie(client, name, value, path="/"):
    """Replace a cookie in the jar. Setting one next to the existing cookie would send both."""
    client.cookies.delete(name)
    client.cookies.set(name, value, domain="localhost.local", path=path)  # how http.cookiejar names the host "localhost"


def _csrf(client):
    return client.cookies.get("csrf_token")


def _cookie_attrs(response, name):
    """The Set-Cookie header for one cookie, as lower-cased text."""
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(f"{name}="):
            return header.lower()
    raise AssertionError(f"no {name} cookie was set")


# ── cookies ──────────────────────────────────────────────────────────────────
@needs_mongo
def test_signing_in_sets_httponly_session_cookies_with_the_right_scope_and_lifetime(client):
    _signup(client)
    client.cookies.clear()
    response = client.post(f"{BASE}/signin", json={"email": "alice@example.com", "password": STRONG})
    settings = get_settings()

    access, refresh, csrf = (_cookie_attrs(response, n) for n in ("access_token", "refresh_token", "csrf_token"))
    assert "httponly" in access and "path=/;" in access + ";" and f"max-age={settings.ACCESS_TOKEN_MINUTES * 60}" in access
    assert "httponly" in refresh and "path=/api/v1/auth/local" in refresh  # only sent to the auth routes
    assert f"max-age={settings.REFRESH_TOKEN_DAYS * 86400}" in refresh
    assert "httponly" not in csrf  # the page needs to send it back
    assert "samesite=lax" in access and "samesite=lax" in refresh and "secure" not in access  # development defaults

    body = response.json()
    assert body["csrf_token"] == client.cookies.get("csrf_token") and body["access_token"] and body["user"]["email"] == "alice@example.com"


@needs_mongo
def test_the_cookie_flags_follow_the_settings(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "COOKIE_SECURE", True)
    monkeypatch.setattr(get_settings(), "COOKIE_SAMESITE", "none")
    monkeypatch.setattr(get_settings(), "COOKIE_DOMAIN", "example.com")
    response = _signup(client)
    for name in ("access_token", "refresh_token", "csrf_token"):
        attrs = _cookie_attrs(response, name)
        assert "secure" in attrs and "samesite=none" in attrs and "domain=example.com" in attrs


@needs_mongo
def test_the_access_token_is_short_lived(client):
    token = _signup(client).json()["access_token"]
    claims = jwt.decode(token, options={"verify_signature": False})
    remaining = claims["exp"] - datetime.now(timezone.utc).timestamp()
    assert get_settings().ACCESS_TOKEN_MINUTES * 60 - 10 < remaining <= get_settings().ACCESS_TOKEN_MINUTES * 60  # not days


@needs_mongo
def test_refresh_tokens_are_stored_only_as_hashes(client):
    _signup(client)
    raw = client.cookies.get("refresh_token", path=f"{BASE}")
    stored = _run(client, lambda: client.db().sessions.find_one({}))
    assert stored["token_hash"] == hash_token(raw) and raw not in str(stored)


@needs_mongo
def test_session_indexes_exist(client):
    async def look():
        return {i["name"]: i async for i in client.db().sessions.list_indexes()}

    indexes = _run(client, look)
    assert indexes["session_token_hash"]["unique"] is True and indexes["session_expiry"]["expireAfterSeconds"] == 0


# ── authenticating with the cookie ───────────────────────────────────────────
@needs_mongo
def test_the_page_can_ask_who_is_signed_in_using_only_cookies(client):
    _signup(client)
    me = client.get(f"{BASE}/me")  # no Authorization header
    assert me.status_code == 200 and me.json()["user"]["email"] == "alice@example.com"
    assert me.json()["csrf_token"] == _csrf(client)  # how a reloaded page gets its csrf token back

    client.cookies.clear()
    anonymous = client.get(f"{BASE}/me")
    assert anonymous.status_code == 401 and anonymous.json()["detail"] == "Not authenticated"  # was 403 before


@needs_mongo
def test_a_bearer_token_still_works_for_api_clients_and_needs_no_csrf_token(client):
    token = _signup(client).json()["access_token"]
    client.cookies.clear()
    response = client.put(f"{BASE}/timezone", json={"timezone": "Asia/Tokyo"}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


# ── CSRF ─────────────────────────────────────────────────────────────────────
@needs_mongo
def test_changing_requests_need_the_csrf_header_when_authenticated_by_cookie(client):
    _signup(client)
    change = lambda **headers: client.put(f"{BASE}/timezone", json={"timezone": "Asia/Tokyo"}, headers=headers)

    assert change().status_code == 403                                    # a cross-site form post would look like this
    assert change(**{"X-CSRF-Token": "forged"}).status_code == 403
    assert change(**{"X-CSRF-Token": _csrf(client)}).status_code == 200
    assert client.get(f"{BASE}/me").status_code == 200                      # reading needs none


@needs_mongo
def test_a_request_from_another_origin_is_refused_even_with_the_right_csrf_token(client):
    _signup(client)
    token = {"X-CSRF-Token": _csrf(client)}
    evil = client.put(f"{BASE}/timezone", json={"timezone": "Asia/Tokyo"}, headers={**token, "Origin": "https://evil.example"})
    assert evil.status_code == 403 and "Origin" in evil.json()["detail"]
    ours = client.put(f"{BASE}/timezone", json={"timezone": "Asia/Tokyo"}, headers={**token, "Origin": ORIGIN})
    assert ours.status_code == 200


# ── refresh ──────────────────────────────────────────────────────────────────
@needs_mongo
def test_an_expired_access_cookie_is_replaced_by_refreshing(client):
    _signup(client)
    expired = auth_utils.create_access_token({"sub": "alice@example.com"}, expires_delta=timedelta(seconds=-5))
    _put_cookie(client, "access_token", expired)
    assert client.get(f"{BASE}/me").status_code == 401

    refreshed = client.post(f"{BASE}/refresh")
    assert refreshed.status_code == 200 and refreshed.json()["user"]["email"] == "alice@example.com"
    assert refreshed.json()["csrf_token"] == _csrf(client)
    assert client.get(f"{BASE}/me").status_code == 200  # the new cookie works


@needs_mongo
def test_each_refresh_token_works_once_and_a_replayed_one_ends_the_session(client, monkeypatch):
    monkeypatch.setattr(SessionModel, "GRACE_SECONDS", 0)
    _signup(client)
    first = client.cookies.get("refresh_token", path=BASE)

    assert client.post(f"{BASE}/refresh").status_code == 200
    second = client.cookies.get("refresh_token", path=BASE)
    assert second and second != first  # rotated

    _put_cookie(client, "refresh_token", first, BASE)  # an attacker replays the old token
    assert client.post(f"{BASE}/refresh").status_code == 401

    _put_cookie(client, "refresh_token", second, BASE)  # and the real owner's newer token is revoked too
    assert client.post(f"{BASE}/refresh").status_code == 401
    assert _run(client, lambda: client.db().sessions.count_documents({})) == 0


@needs_mongo
def test_two_tabs_refreshing_at_the_same_moment_do_not_log_the_user_out(client):
    _signup(client)
    first = client.cookies.get("refresh_token", path=BASE)
    assert client.post(f"{BASE}/refresh").status_code == 200
    second = client.cookies.get("refresh_token", path=BASE)

    _put_cookie(client, "refresh_token", first, BASE)  # the other tab still holds the previous token
    assert client.post(f"{BASE}/refresh").status_code == 200  # accepted inside the grace window

    _put_cookie(client, "refresh_token", second, BASE)
    assert client.post(f"{BASE}/refresh").status_code == 200  # and nobody was revoked


@needs_mongo
def test_refreshing_without_a_valid_session_clears_the_cookies(client):
    for setup in (lambda: client.cookies.clear(), lambda: _put_cookie(client, "refresh_token", "garbage-token", BASE)):
        client.cookies.clear()
        setup()
        response = client.post(f"{BASE}/refresh")
        assert response.status_code == 401 and response.json()["code"] == "session_expired"
        cleared = " ".join(response.headers.get_list("set-cookie")).lower()
        assert all(name in cleared for name in ("access_token", "refresh_token", "csrf_token")) and "max-age=0" in cleared


@needs_mongo
def test_an_expired_session_cannot_be_refreshed(client):
    _signup(client)
    _run(client, lambda: client.db().sessions.update_many({}, {"$set": {"expires_at": datetime.utcnow() - timedelta(seconds=1)}}))
    assert client.post(f"{BASE}/refresh").status_code == 401


@needs_mongo
def test_a_deleted_account_cannot_keep_refreshing(client):
    _signup(client)
    _run(client, lambda: client.db().users.delete_many({}))
    assert client.post(f"{BASE}/refresh").status_code == 401
    assert _run(client, lambda: client.db().sessions.count_documents({})) == 0


@needs_mongo
def test_refreshing_is_rate_limited_per_address(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "REFRESH_MAX_PER_IP_PER_MINUTE", 2)
    _signup(client)
    assert client.post(f"{BASE}/refresh").status_code == 200
    assert client.post(f"{BASE}/refresh").status_code == 200
    limited = client.post(f"{BASE}/refresh")
    assert limited.status_code == 429 and "Retry-After" in limited.headers


@needs_mongo
def test_a_refresh_request_from_another_origin_is_refused(client):
    _signup(client)
    assert client.post(f"{BASE}/refresh", headers={"Origin": "https://evil.example"}).status_code == 403


# ── signing out ──────────────────────────────────────────────────────────────
@needs_mongo
def test_signing_out_ends_the_session_everywhere_and_clears_the_cookies(client):
    _signup(client)
    old_refresh = client.cookies.get("refresh_token", path=BASE)

    response = client.post(f"{BASE}/signout")
    assert response.status_code == 200
    cleared = " ".join(response.headers.get_list("set-cookie")).lower()
    assert all(name in cleared for name in ("access_token", "refresh_token", "csrf_token")) and "max-age=0" in cleared

    _put_cookie(client, "refresh_token", old_refresh, BASE)  # a stolen copy no longer works
    assert client.post(f"{BASE}/refresh").status_code == 401
    assert _run(client, lambda: client.db().sessions.count_documents({})) == 0


@needs_mongo
def test_one_user_signing_out_does_not_affect_another(client):
    _signup(client, "alice@example.com")
    alice_refresh = client.cookies.get("refresh_token", path=BASE)
    client.cookies.clear()
    _signup(client, "bob@example.com")

    client.post(f"{BASE}/signout")  # bob signs out
    client.cookies.clear()
    _put_cookie(client, "refresh_token", alice_refresh, BASE)
    assert client.post(f"{BASE}/refresh").status_code == 200


# ── settings ─────────────────────────────────────────────────────────────────
def _settings(**changes):
    from test_config import VALID  # the smallest valid configuration

    return Settings(_env_file=None, **{**VALID, **changes})


def test_unsafe_cookie_settings_are_reported_at_startup():
    assert any("COOKIE_SECURE" in p for p in _settings(COOKIE_SAMESITE="none").problems())
    assert any("COOKIE_SECURE" in p for p in _settings(FRONTEND_URL="https://app.example.com").problems())
    assert _settings(FRONTEND_URL="https://app.example.com", COOKIE_SECURE=True, COOKIE_SAMESITE="none").problems() == []
    with pytest.raises(Exception):
        _settings(COOKIE_SAMESITE="sometimes")
    assert _settings(COOKIE_DOMAIN="").COOKIE_DOMAIN is None
