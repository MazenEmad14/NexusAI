"""Regression tests for the P0 security fixes (see REFACTOR_PLAN.md section 1)."""
import asyncio
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── 1.1 per-request Google clients ───────────────────────────────────────────
def test_google_clients_are_never_shared_between_users(monkeypatch):
    from agent.google_clients import gmail_for_request
    from helpers.google_context import set_google_token
    from stores.gmail.GmailService import GmailService

    def fake_auth(self, token):
        time.sleep(0.01)  # widen the race window
        self.service = f"service-for-{token['user']}"
        return token

    monkeypatch.setattr(GmailService, "authenticate_from_dict", fake_auth)

    seen = {}

    def run(user):
        set_google_token({"user": user})  # what a request does before invoking the graph
        client = gmail_for_request()
        time.sleep(0.02)  # another user authenticates while this one is in flight
        seen[user] = client.service

    threads = [threading.Thread(target=run, args=(u,)) for u in ("alice", "bob", "carol")]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert seen == {u: f"service-for-{u}" for u in ("alice", "bob", "carol")}


def test_graph_nodes_refuse_to_act_without_the_users_own_token():
    from agent import AgentDeps
    from agent.nodes import actions
    from helpers.google_context import set_google_token

    set_google_token(None)
    state = {"action_details": {"recipient": "x@example.com", "subject": "s", "body": "b"}, "metadata": {}}
    result = actions.send_email_node(state, AgentDeps())
    assert result["status"] == "error"
    assert "connect your Google account" in result["result"]


# ── 1.2/1.3 settings ─────────────────────────────────────────────────────────
def test_jwt_secret_must_be_long_enough():
    from helpers.config import Settings

    with pytest.raises(ValueError):
        Settings.validate_jwt_secret("short")


# ── 1.4 auth on every protected route, project scoping, file resolution ──────
def test_protected_routes_reject_anonymous_requests():
    import main
    from fastapi.testclient import TestClient

    public = {"/api/v1", "/api/v1/", "/api/v1/auth/google/callback", "/api/v1/agent/notifications"}
    client = TestClient(main.app)
    checked = 0
    for route in main.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1") or path.startswith("/api/v1/auth/local") or path in public:
            continue
        method = next(m for m in route.methods if m not in ("HEAD", "OPTIONS"))
        url = path.replace("{project_id}", "x").replace("{thread_id}", "t")
        assert client.request(method, url).status_code in (401, 403), f"{method} {path} is not protected"
        checked += 1
    assert checked >= 15


def test_project_ids_are_namespaced_per_user_and_reject_traversal():
    from helpers.project_scope import scoped_project_id

    assert scoped_project_id("u1", "default") != scoped_project_id("u2", "default")
    assert scoped_project_id("u1", "default").isalnum()
    for bad in ("../x", "a/b", "a b", "", "x" * 65, "..\\x"):
        with pytest.raises(ValueError):
            scoped_project_id("u1", bad)


def test_data_analysis_only_reads_files_inside_the_users_project(tmp_path, monkeypatch):
    from agent.nodes import data_analysis as dan

    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "sales.csv").write_text("a,b\n1,2\n")
    (tmp_path / "secret.csv").write_text("a\n1\n")

    monkeypatch.setattr(dan, "ProjectController", lambda: SimpleNamespace(get_project_path=lambda pid: str(project_dir)))
    state = {"user_id": "u1", "project_id": "default"}

    assert dan.resolve_user_file({**state, "file_path": "sales.csv"}).endswith("sales.csv")
    assert dan.resolve_user_file({**state, "file_path": "C:\\data\\uploads\\sales.csv"}).endswith("sales.csv")
    assert dan.resolve_user_file({**state, "file_path": "../secret.csv"}) is None  # reduced to secret.csv, not in project
    assert dan.resolve_user_file({**state, "file_path": "/etc/passwd"}) is None
    assert dan.resolve_user_file({**state, "file_path": "run.py"}) is None


# ── 1.5 no LLM-generated code is executed ───────────────────────────────────
def test_analysis_plan_rejects_unsafe_operations_and_unknown_columns():
    import pandas as pd
    from agent.nodes.data_analysis import _execute_plan

    df = pd.DataFrame({"Region": ["a", "b"], "Sales": ["1,000", "2"]})
    table, chart, x = _execute_plan(df, {"op": "aggregate", "column": "Sales", "agg": "sum", "group_by": "Region", "chart": "bar"})
    assert table["Sales"].sum() == 1002 and chart == "bar" and x == "Region"

    for bad in (
        {"op": "exec", "code": "__import__('os').system('x')"},
        {"op": "aggregate", "column": "__import__('os')", "agg": "sum"},
        {"op": "aggregate", "column": "Sales", "agg": "apply"},
    ):
        with pytest.raises(ValueError):
            _execute_plan(df, bad)


def test_python_repl_is_no_longer_used():
    source = (Path(__file__).resolve().parents[1] / "agent" / "nodes" / "data_analysis.py").read_text(encoding="utf-8")
    assert "PythonREPL" not in source and "exec(" not in source and "eval(" not in source


# ── 1.6 OAuth ────────────────────────────────────────────────────────────────
def test_oauth_callback_rejects_unknown_state_and_stores_nothing():
    import main
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    response = client.get("/api/v1/auth/google/callback", params={"code": "abc", "state": "forged"})
    assert response.status_code == 400


# ── 1.7 per-user notifications ───────────────────────────────────────────────
def test_notifications_are_per_user_and_tickets_are_single_use():
    from helpers import event_bus

    async def scenario():
        await event_bus.publish("alice", "for alice")
        assert event_bus.get_user_queue("bob").empty()
        assert await event_bus.get_user_queue("alice").get() == "for alice"

    asyncio.run(scenario())

    ticket = event_bus.issue_ticket("alice")
    assert event_bus.redeem_ticket(ticket) == "alice"
    assert event_bus.redeem_ticket(ticket) is None
    assert event_bus.redeem_ticket("made-up") is None


# ── 1.8 approvals and outgoing mail ──────────────────────────────────────────
def _paused_snapshot(token="tok"):
    return SimpleNamespace(
        values={"status": "pending", "metadata": {"resume_token": token}},
        next=("send_email",),
    )


def test_resume_requires_the_matching_token():
    from routes.agent import ThreadStateLoadError, _validate_resume_state

    assert _validate_resume_state(_paused_snapshot(), "t", "tok")
    for supplied in (None, "", "wrong"):
        with pytest.raises(ThreadStateLoadError) as exc:
            _validate_resume_state(_paused_snapshot(), "t", supplied)
        assert exc.value.code == "stale_approval"


def test_email_recipients_are_validated_and_headers_cannot_be_injected():
    from helpers.validation import has_header_injection, parse_recipients
    from stores.gmail.GmailService import GmailService

    assert parse_recipients("a@example.com, b@example.com") == ["a@example.com", "b@example.com"]
    for bad in ("", "nobody", "a@b", "a@example.com\nBcc: evil@example.com", None):
        assert parse_recipients(bad) is None

    service = GmailService()
    service.service = object()  # would raise if a send were attempted
    from helpers.exceptions import ValidationFailed

    with pytest.raises(ValidationFailed):
        service.send_email("a@example.com\nBcc: evil@example.com", "hi", "body")
    with pytest.raises(ValidationFailed):
        service.send_email("a@example.com", "hi\r\nBcc: evil@example.com", "body")
    assert has_header_injection("x\ny")


# ── 1.9 untrusted email content ──────────────────────────────────────────────
def test_task_extraction_output_is_bounded():
    from controllers.task_extractor import TaskExtractorController

    raw = [{"description": "x" * 1000, "deadline": "d" * 100, "priority": "weird"}, {"description": ""}, "junk"] + [
        {"description": f"t{i}"} for i in range(30)
    ]
    clean = TaskExtractorController._sanitize(raw)
    assert len(clean) == 8  # only the first 10 raw items are considered; 2 of them are invalid
    assert len(clean[0]["description"]) == 200 and len(clean[0]["deadline"]) == 40 and clean[0]["priority"] == "medium"


# ── token encryption at rest ─────────────────────────────────────────────────
def test_token_encryption_round_trip_and_legacy_plaintext(monkeypatch):
    from cryptography.fernet import Fernet
    from helpers import token_crypto

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(token_crypto, "get_settings", lambda: SimpleNamespace(TOKEN_ENCRYPTION_KEY=key))
    token = {"refresh_token": "r", "client_id": "c"}
    stored = token_crypto.encrypt_token(token)
    assert "refresh_token" not in stored
    assert token_crypto.decrypt_token(stored) == token
    assert token_crypto.decrypt_token(token) == token  # legacy plaintext still readable
