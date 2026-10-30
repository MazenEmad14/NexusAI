"""Sign-up / sign-in behaviour (plan 2.11). Runs the real router against a throwaway MongoDB
database; skipped when none is reachable."""
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import auth_utils
from helpers.auth_utils import get_current_user
from helpers.config import get_settings
from helpers.validation import password_problem
from models.RateLimitModel import RateLimitModel
from models.UserModel import UserModel
from models.indexes import ensure_all_indexes
from routes import local_auth

MONGO_URL = "mongodb://localhost:27017/"
STRONG = "correct horse 42"


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
    # The client address comes from a header so tests can act as different machines.
    monkeypatch.setattr(local_auth, "_client_ip", lambda request: request.headers.get("x-test-ip", "10.0.0.1"))

    app = FastAPI()
    app.include_router(local_auth.local_auth_router)

    @app.get("/me")
    async def me(user: dict = Depends(get_current_user)):
        return {"email": user["email"]}

    db_name = f"test_auth_{uuid.uuid4().hex[:8]}"

    @app.on_event("startup")
    async def start():
        app.mongo_conn = AsyncIOMotorClient(MONGO_URL)
        app.mongo_db = app.mongo_conn[db_name]
        await ensure_all_indexes(app.mongo_db)

    @app.on_event("shutdown")
    async def stop():
        await app.mongo_conn.drop_database(db_name)
        app.mongo_conn.close()

    with TestClient(app) as test_client:
        test_client.db = lambda: app.mongo_db
        yield test_client


def _signup(client, email="alice@example.com", password=STRONG, name="Alice", ip="10.0.0.1"):
    return client.post("/api/v1/auth/local/signup", json={"name": name, "email": email, "password": password}, headers={"x-test-ip": ip})


def _signin(client, email="alice@example.com", password=STRONG, ip="10.0.0.1"):
    return client.post("/api/v1/auth/local/signin", json={"email": email, "password": password}, headers={"x-test-ip": ip})


def _run(client, coro_fn):
    return client.portal.call(coro_fn)


# ── pure helpers ─────────────────────────────────────────────────────────────
def test_password_rules():
    assert password_problem(STRONG, "alice@example.com", "Alice") is None
    assert "at least 8" in password_problem("short1")
    assert "at most 72" in password_problem("x" * 73 + "1a")
    assert "too common" in password_problem("password123")
    assert "too common" in password_problem("aaaaaaaaaa")
    assert "email or name" in password_problem("alicesmith2024!", "alicesmith@example.com", "Alice")
    assert "email or name" in password_problem("alice smith", "a@example.com", "Alice Smith")


def test_password_hashing_is_robust_and_compatible_with_hashes_from_passlib():
    from passlib.context import CryptContext

    legacy = CryptContext(schemes=["bcrypt"]).hash("old password 1")  # what earlier versions stored
    assert auth_utils.verify_password("old password 1", legacy) is True
    assert auth_utils.verify_password("wrong", legacy) is False

    fresh = auth_utils.get_password_hash("new password 1")
    assert auth_utils.verify_password("new password 1", fresh) and not auth_utils.verify_password("x", fresh)

    assert auth_utils.verify_password("anything", "not-a-hash") is False  # malformed hash must not raise
    assert auth_utils.verify_password("x" * 100, fresh) is False  # over-long input must not raise


# ── emails ───────────────────────────────────────────────────────────────────
@needs_mongo
def test_emails_are_case_insensitive(client):
    first = _signup(client, email="Alice@Example.COM")
    assert first.status_code == 200 and first.json()["user"]["email"] == "alice@example.com"

    assert _signup(client, email="ALICE@example.com").status_code == 409  # same account, different case
    for variant in ("alice@example.com", "ALICE@EXAMPLE.COM", " Alice@Example.com "):
        assert _signin(client, email=variant).status_code == 200, variant

    stored = _run(client, lambda: client.db().users.count_documents({}))
    assert stored == 1


@needs_mongo
def test_tokens_with_a_mixed_case_subject_still_work(client):
    _signup(client)
    token = auth_utils.create_access_token({"sub": "Alice@Example.com"})  # as issued by older versions
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).json() == {"email": "alice@example.com"}


@needs_mongo
def test_legacy_mixed_case_accounts_are_migrated_at_startup(client):
    async def scenario():
        db = client.db()
        await db.users.insert_many([
            {"email": "Bob@Example.com", "name": "Bob", "password_hash": "x"},
            {"email": "Carol@Example.com", "name": "Carol", "password_hash": "x"},
            {"email": "carol@example.com", "name": "Carol 2", "password_hash": "x"},  # collides once lower-cased
        ])
        changed = await UserModel(db).normalize_existing_emails()
        return changed, sorted([d["email"] async for d in db.users.find({})])

    changed, emails = _run(client, scenario)
    assert changed == 1
    assert emails == ["Carol@Example.com", "bob@example.com", "carol@example.com"]


# ── no account enumeration ───────────────────────────────────────────────────
@needs_mongo
def test_unknown_email_and_wrong_password_look_identical(client):
    _signup(client)
    unknown = _signin(client, email="nobody@example.com", password="whatever 12")
    wrong = _signin(client, password="wrong password 1")

    assert unknown.status_code == wrong.status_code == 401  # not 404 for unknown accounts
    assert unknown.json() == wrong.json() == {"detail": local_auth.INVALID_CREDENTIALS}


@needs_mongo
def test_an_unknown_email_still_costs_a_password_check(client, monkeypatch):
    seen = []
    real = auth_utils.verify_password

    def spy(plain, hashed):
        seen.append(hashed)
        return real(plain, hashed)

    monkeypatch.setattr(auth_utils, "verify_password", spy)
    _signin(client, email="nobody@example.com", password="whatever 12")
    assert seen == [auth_utils.DUMMY_PASSWORD_HASH]


# ── throttling ───────────────────────────────────────────────────────────────
@needs_mongo
def test_repeated_failures_lock_the_account_for_that_client_even_with_the_right_password(client):
    _signup(client)
    limit = get_settings().LOGIN_MAX_FAILURES_PER_ACCOUNT
    for _ in range(limit):
        assert _signin(client, password="wrong password 1").status_code == 401

    blocked = _signin(client)  # correct password, but the limit is reached
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    assert "Try again in" in blocked.json()["detail"]

    # A stranger's failures must not lock the real owner out: another address is unaffected.
    assert _signin(client, ip="10.9.9.9").status_code == 200


@needs_mongo
def test_a_successful_sign_in_clears_the_failure_count(client):
    _signup(client)
    limit = get_settings().LOGIN_MAX_FAILURES_PER_ACCOUNT
    for _ in range(limit - 1):
        _signin(client, password="wrong password 1")
    assert _signin(client).status_code == 200
    for _ in range(limit - 1):
        assert _signin(client, password="wrong password 1").status_code == 401  # not blocked: counter was reset


@needs_mongo
def test_one_address_cannot_guess_across_many_accounts(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "LOGIN_MAX_FAILURES_PER_IP", 3)
    for i in range(3):
        assert _signin(client, email=f"user{i}@example.com", password="wrong password 1", ip="10.5.5.5").status_code == 401
    assert _signin(client, email="user99@example.com", password="x" * 9, ip="10.5.5.5").status_code == 429
    assert _signin(client, email="user99@example.com", password="x" * 9, ip="10.6.6.6").status_code == 401


@needs_mongo
def test_signups_are_throttled_per_address(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "SIGNUP_MAX_PER_IP_PER_HOUR", 2)
    assert _signup(client, email="a1@example.com", ip="10.7.7.7").status_code == 200
    assert _signup(client, email="a1@example.com", ip="10.7.7.7").status_code == 409  # attempts count, even failed ones
    third = _signup(client, email="a3@example.com", ip="10.7.7.7")
    assert third.status_code == 429 and "Retry-After" in third.headers
    assert _signup(client, email="a3@example.com", ip="10.8.8.8").status_code == 200


@needs_mongo
def test_rate_limit_windows_expire_and_have_a_ttl_index(client):
    async def scenario():
        db = client.db()
        limits = await RateLimitModel.create_instance(db)
        assert await limits.hit("k", 60) == 1 and await limits.hit("k", 60) == 2
        count, ends = await limits.status("k")
        assert count == 2 and ends > datetime.utcnow()

        await db.rate_limits.update_one({"_id": "k"}, {"$set": {"expires_at": datetime.utcnow() - timedelta(seconds=1)}})
        assert await limits.status("k") == (0, None)  # expired counters are ignored even before the TTL sweep
        assert await limits.hit("k", 60) == 1  # and a new window starts fresh

        ttl = [i async for i in db.rate_limits.list_indexes() if i.get("expireAfterSeconds") == 0]
        return len(ttl)

    assert _run(client, scenario) == 1


# ── sign-up validation ───────────────────────────────────────────────────────
@needs_mongo
def test_weak_passwords_get_readable_errors(client):
    for password, fragment in (("short1", "at least 8"), ("password123", "too common"), ("x" * 80, "at most 72")):
        response = _signup(client, password=password)
        assert response.status_code == 400
        assert fragment in response.json()["detail"]  # a plain string the UI can show as-is
    assert _run(client, lambda: client.db().users.count_documents({})) == 0


@needs_mongo
def test_sign_up_then_sign_in_round_trip(client):
    assert _signup(client).status_code == 200
    token = _signin(client).json()["access_token"]
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200


# ── time zone preference (plan 2.6) ──────────────────────────────────────────
@needs_mongo
def test_the_users_time_zone_is_saved_and_validated(client):
    token = _signup(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    saved = client.put("/api/v1/auth/local/timezone", json={"timezone": "Asia/Tokyo"}, headers=headers)
    assert saved.status_code == 200 and saved.json() == {"timezone": "Asia/Tokyo"}
    assert _run(client, lambda: client.db().users.find_one({"email": "alice@example.com"}))["timezone"] == "Asia/Tokyo"

    for bad in ("Mars/Base", "../etc/passwd", "", "x" * 100):
        response = client.put("/api/v1/auth/local/timezone", json={"timezone": bad}, headers=headers)
        assert response.status_code in (400, 422), bad
    assert _run(client, lambda: client.db().users.find_one({"email": "alice@example.com"}))["timezone"] == "Asia/Tokyo"


@needs_mongo
def test_setting_a_time_zone_requires_signing_in(client):
    assert client.put("/api/v1/auth/local/timezone", json={"timezone": "Asia/Tokyo"}).status_code in (401, 403)
