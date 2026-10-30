"""Upload validation and processing (plan 2.12). Runs the real routes against a throwaway MongoDB
database and a temporary upload folder; skipped when no MongoDB is reachable."""
import io
import sys
import uuid
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from controllers.BaseController import BaseController
from controllers.DataController import DataController
from helpers.auth_utils import get_current_user
from helpers.file_types import allowed_extensions, content_matches, is_indexable
from routes import data as data_routes

MONGO_URL = "mongodb://localhost:27017/"
PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"


def _mongo_available() -> bool:
    import pymongo

    try:
        pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=1000).server_info()
        return True
    except Exception:
        return False


needs_mongo = pytest.mark.skipif(not _mongo_available(), reason="MongoDB is not reachable")


@pytest.fixture
def client(tmp_path, monkeypatch):
    """The data routes with auth stubbed, an isolated upload folder and a throwaway database."""
    original_init = BaseController.__init__

    def patched_init(self):
        original_init(self)
        self.files_dir = str(tmp_path)

    monkeypatch.setattr(BaseController, "__init__", patched_init)

    app = FastAPI()
    app.include_router(data_routes.data_router)
    app.dependency_overrides[get_current_user] = lambda: {"_id": "u1", "email": "a@example.com"}
    db_name = f"test_uploads_{uuid.uuid4().hex[:8]}"

    @app.on_event("startup")
    async def start():
        conn = AsyncIOMotorClient(MONGO_URL)
        app.state.services = SimpleNamespace(mongo_conn=conn, mongo_db=conn[db_name])

    @app.on_event("shutdown")
    async def stop():
        await app.state.services.mongo_conn.drop_database(db_name)
        app.state.services.mongo_conn.close()

    with TestClient(app) as test_client:
        test_client.app_db = lambda: app.state.services.mongo_db
        test_client.upload_dir = tmp_path
        yield test_client


def _upload(client, name, content, content_type="application/octet-stream", project="default"):
    return client.post(f"/api/v1/data/upload/{project}", files={"file": (name, io.BytesIO(content), content_type)})


def _count(client, collection):
    async def count():
        return await client.app_db()[collection].count_documents({})

    return client.portal.call(count)


def _files_on_disk(client):
    return [p for p in client.upload_dir.rglob("*") if p.is_file()]


# ── pure rules ───────────────────────────────────────────────────────────────
def test_allowed_extensions_follow_the_configured_content_types():
    assert allowed_extensions(["text/plain", "application/pdf"]) == {".txt", ".pdf"}
    assert ".pdf" not in allowed_extensions(["text/plain"])
    assert allowed_extensions([]) == set()


def test_content_sniffing_rules():
    assert content_matches(".pdf", PDF_BYTES)
    assert not content_matches(".pdf", b"just some text")
    assert content_matches(".txt", "مرحبا بالعالم".encode("utf-8"))
    assert not content_matches(".txt", b"abc\x00def")
    assert not content_matches(".txt", b"\xff\xfe\xfa invalid utf-8 \xff" + b"x" * 100)
    assert content_matches(".xlsx", b"PK\x03\x04rest")
    assert not content_matches(".xlsx", b"plain text")
    assert content_matches(".xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest")
    assert not content_matches(".exe", b"MZ")
    assert not content_matches(".txt", b"")
    assert is_indexable("a.txt") and is_indexable("a.PDF") and not is_indexable("a.csv")


def test_file_names_are_cleaned_and_random_keys_are_unpredictable_looking():
    controller = DataController()
    assert controller.get_clean_file_name("My File.PDF") == "My_File.pdf"
    assert controller.get_clean_file_name("../../etc/passwd") == "etcpasswd"
    assert controller.get_clean_file_name("..") == "file"
    key = controller.generate_random_string()
    assert len(key) == 12 and key.isalnum() and key == key.lower()
    assert controller.generate_random_string() != key


# ── upload route ─────────────────────────────────────────────────────────────
@needs_mongo
def test_valid_files_are_stored_and_report_whether_they_are_indexable(client):
    text = _upload(client, "notes.txt", b"hello world", "text/plain")
    assert text.status_code == 200 and text.json()["indexable"] is True
    assert len(_files_on_disk(client)) == 1 and _count(client, "assets") == 1

    sheet = _upload(client, "sales.csv", b"a,b\n1,2\n", "text/csv")
    assert sheet.status_code == 200 and sheet.json()["indexable"] is False

    upper = _upload(client, "REPORT.PDF", PDF_BYTES, "application/pdf")
    assert upper.status_code == 200 and upper.json()["file_path"].endswith("_REPORT.pdf")  # extension normalised


@needs_mongo
def test_a_matching_content_type_header_cannot_smuggle_the_wrong_content(client):
    cases = [
        ("evil.pdf", b"#!/bin/sh\nrm -rf /\n", "application/pdf"),       # claims PDF, is a script
        ("notes.txt", b"abc\x00\x01\x02binary", "text/plain"),             # claims text, is binary
        ("book.xlsx", b"this is not a zip", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("run.exe", b"MZ\x90\x00", "text/plain"),                         # right header, wrong extension
        ("noextension", b"hello", "text/plain"),
    ]
    for name, content, content_type in cases:
        response = _upload(client, name, content, content_type)
        assert response.status_code == 400, name
        assert response.json()["signal"] == "file_type_not_supported", name
    assert _files_on_disk(client) == []


@needs_mongo
def test_the_declared_content_type_is_not_what_decides(client):
    # Browsers often send application/octet-stream; a real PDF must still be accepted.
    assert _upload(client, "real.pdf", PDF_BYTES, "application/octet-stream").status_code == 200


@needs_mongo
def test_empty_files_are_rejected(client):
    response = _upload(client, "empty.txt", b"", "text/plain")
    assert response.status_code == 400 and response.json()["signal"] == "file_empty"
    assert _files_on_disk(client) == []


@needs_mongo
def test_oversized_upload_is_rejected_when_the_size_is_known(client, monkeypatch):
    monkeypatch.setattr(DataController, "max_upload_bytes", 100)
    response = _upload(client, "big.txt", b"x" * 5000, "text/plain")
    assert response.status_code == 400 and response.json()["signal"] == "file_size_exceeded"
    assert _files_on_disk(client) == []


@needs_mongo
def test_the_size_limit_is_enforced_while_streaming_when_the_size_is_unknown(client, monkeypatch):
    original = DataController.validate_uploaded_file

    def size_unknown(self, file):
        file.size = None  # as if the client did not declare a length
        return original(self, file)

    monkeypatch.setattr(DataController, "validate_uploaded_file", size_unknown)
    monkeypatch.setattr(DataController, "max_upload_bytes", 100)

    response = _upload(client, "big.txt", b"x" * 20000, "text/plain")

    assert response.status_code == 413 and response.json()["signal"] == "file_size_exceeded"
    assert _files_on_disk(client) == []  # the partial file is removed
    assert _count(client, "assets") == 0  # and no record was created for it


@needs_mongo
def test_no_orphan_file_is_left_when_the_database_write_fails(client, monkeypatch):
    from models.AssetModel import AssetModel

    async def failing(self, asset):
        raise RuntimeError("database down")

    monkeypatch.setattr(AssetModel, "create_asset", failing)
    response = _upload(client, "notes.txt", b"hello", "text/plain")
    assert response.status_code == 500
    assert _files_on_disk(client) == []


# ── process route ────────────────────────────────────────────────────────────
def _process(client, **body):
    return client.post("/api/v1/data/process/default", json={"chunk_size": 50, "overlap_size": 5, **body})


def _chunk_count(client):
    return _count(client, "chunks")


@needs_mongo
def test_processing_twice_replaces_chunks_instead_of_duplicating_them(client):
    file_id = _upload(client, "notes.txt", b"Some reasonably long text. " * 40, "text/plain").json()["file_id"]

    first = _process(client, file_id=file_id).json()
    count_after_first = _chunk_count(client)
    second = _process(client, file_id=file_id).json()

    assert first["processed_files"] == 1 and first["inserted_chunks"] == count_after_first > 1
    assert second["inserted_chunks"] == count_after_first
    assert _chunk_count(client) == count_after_first


@needs_mongo
def test_spreadsheets_are_skipped_not_treated_as_failures(client):
    _upload(client, "notes.txt", b"Some reasonably long text. " * 40, "text/plain")
    _upload(client, "sales.csv", b"a,b\n1,2\n", "text/csv")

    response = _process(client)

    body = response.json()
    assert response.status_code == 200 and body["processed_files"] == 1
    statuses = {r["file"].split("_", 1)[1]: r["status"] for r in body["results"]}
    assert statuses == {"notes.txt": "processed", "sales.csv": "skipped"}


@needs_mongo
def test_only_spreadsheets_means_nothing_to_index_but_not_an_error(client):
    file_id = _upload(client, "sales.csv", b"a,b\n1,2\n", "text/csv").json()["file_id"]
    response = _process(client, file_id=file_id)
    assert response.status_code == 200
    assert response.json()["processed_files"] == 0 and response.json()["results"][0]["reason"] == "not_indexable"


@needs_mongo
def test_one_unreadable_file_does_not_hide_or_undo_the_others(client):
    good = _upload(client, "good.txt", b"Plenty of readable text here. " * 30, "text/plain")
    # Valid UTF-8 in the sniffed window, invalid bytes later: passes upload, fails when read.
    bad_content = b"a" * 9000 + b"\xff\xfe\xfa" + b"b" * 100
    bad = _upload(client, "bad.txt", bad_content, "text/plain")
    assert good.status_code == 200 and bad.status_code == 200

    response = _process(client)

    body = response.json()
    assert response.status_code == 200 and body["processed_files"] == 1 and body["inserted_chunks"] > 0
    statuses = {r["file"].split("_", 1)[1]: (r["status"], r.get("reason")) for r in body["results"]}
    assert statuses == {"good.txt": ("processed", None), "bad.txt": ("failed", "unreadable")}


@needs_mongo
def test_when_every_file_fails_processing_reports_an_error(client):
    file_id = _upload(client, "bad.txt", b"a" * 9000 + b"\xff\xfe\xfa" + b"b" * 100, "text/plain").json()["file_id"]
    response = _process(client, file_id=file_id)
    assert response.status_code == 400
    assert response.json()["signal"] == "processing_failed"
    assert response.json()["results"][0]["reason"] == "unreadable"
