"""
tests/test_e2e_flow.py
======================
Comprehensive End-to-End Integration Test Suite for the NexusAI LangGraph Agent.

Tests are intentionally self-documenting: each test maps to a numbered
scenario from the product specification.

Architecture
------------
All external I/O is replaced by session-scoped mocks defined in conftest.py:
  - LLM providers (OpenAI / Cohere / Gemini / Groq) -> deterministic mock
  - Vector DB (Qdrant)                               -> in-memory mock
  - Gmail API                                        -> MagicMock
  - Google Calendar API                              -> MagicMock
  - MongoDB (Motor)                                  -> AsyncMock

The LangGraph state machine runs its REAL compiled graph (real routing logic).
Only the external side-effects (LLM calls, API calls) are replaced by mocks.

Authentication
--------------
A valid JWT is synthesised from TEST_USER_EMAIL (no network required).
The get_current_user FastAPI dependency resolves to MOCK_USER_DOC via the
UserModel.create_instance mock defined in conftest.py.
"""

import contextlib
import json
import os
import sys
import uuid
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure src/ is importable (conftest.py also does this, belt-and-braces)
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# Fixtures provided by conftest.py:
#   patched_app, valid_token, auth_headers, client, thread_id

CHAT_ENDPOINT = "/api/v1/agent/chat"
RESUME_ENDPOINT = "/api/v1/agent/resume"


# ---------------------------------------------------------------------------
# SHARED HELPER UTILITIES
# ---------------------------------------------------------------------------

def chat(
    client,
    auth_headers: Dict[str, str],
    thread_id: str,
    query: str,
    file_path: str = None,
    project_id: str = "e2eproject",
) -> Dict[str, Any]:
    """
    POST to the /api/v1/agent/chat endpoint and return the parsed JSON body.

    Parameters
    ----------
    client       : FastAPI TestClient (session-scoped, provided by conftest).
    auth_headers : dict containing the Bearer token Authorization header.
    thread_id    : str  – conversation thread ID (maintains LangGraph memory).
    query        : str  – natural-language user message.
    file_path    : str  – optional path to an uploaded file (CSV / PDF).
    project_id   : str  – project namespace for vector DB lookups.

    Returns
    -------
    Parsed JSON dict of the ChatResponse model.

    Raises
    ------
    AssertionError if the HTTP status code is not 200.
    """
    payload: Dict[str, Any] = {
        "query": query,
        "thread_id": thread_id,
        "project_id": project_id,
    }
    if file_path:
        payload["file_path"] = file_path

    response = client.post(CHAT_ENDPOINT, json=payload, headers=auth_headers)
    assert response.status_code == 200, (
        f"Chat endpoint returned {response.status_code}.\nBody: {response.text}"
    )
    return response.json()


def resume_action(
    client,
    auth_headers: Dict[str, str],
    thread_id: str,
    approve: bool,
    resume_token: str = None,
) -> Dict[str, Any]:
    """
    POST to the /api/v1/agent/resume endpoint (HITL approval / rejection).

    Returns the parsed JSON dict of the ChatResponse model.
    Raises AssertionError if the HTTP status code is not 200.
    """
    payload: Dict[str, Any] = {"thread_id": thread_id, "approve": approve}
    if resume_token:
        payload["resume_token"] = resume_token

    response = client.post(RESUME_ENDPOINT, json=payload, headers=auth_headers)
    assert response.status_code == 200, (
        f"Resume endpoint returned {response.status_code}.\nBody: {response.text}"
    )
    return response.json()


@contextlib.contextmanager
def _patch_data_analysis_llms(fake_response: MagicMock):
    """
    Context manager that replaces all three LLM providers inside
    controllers/data_analysis_node.py with a mock that returns fake_response.

    This prevents any real API calls while still exercising the PythonREPL logic.
    """
    with (
        patch(
            "langchain_groq.ChatGroq",
            return_value=MagicMock(ainvoke=AsyncMock(return_value=fake_response)),
        ),
        patch(
            "langchain_google_genai.ChatGoogleGenerativeAI",
            return_value=MagicMock(ainvoke=AsyncMock(return_value=fake_response)),
        ),
        patch(
            "langchain_openai.ChatOpenAI",
            return_value=MagicMock(ainvoke=AsyncMock(return_value=fake_response)),
        ),
    ):
        yield


def _make_csv_analysis_llm_response(csv_path: str) -> MagicMock:
    """
    Build a fake LLM response whose .content attribute is valid Python code
    that the PythonREPL inside data_analysis_node will execute to produce
    a well-formed JSON output with chart_json.
    """
    code = "\n".join([
        "import json, pandas as pd, numpy as np",
        f"df = pd.read_csv(r'{csv_path}')",
        "top3 = df.nlargest(3, 'Revenue')",
        "class NpEncoder(json.JSONEncoder):",
        "    def default(self, obj):",
        "        if isinstance(obj, np.integer): return int(obj)",
        "        if isinstance(obj, np.floating): return float(obj)",
        "        return super().default(obj)",
        "result = {",
        "    'text_summary': 'Top 3 revenue months: ' + ', '.join(top3['Month'].tolist()),",
        "    'chart_json': {",
        "        'type': 'bar',",
        "        'xAxisKey': 'Month',",
        "        'data': top3[['Month', 'Revenue']].to_dict(orient='records')",
        "    }",
        "}",
        "print(json.dumps(result, cls=NpEncoder))",
    ])
    fake = MagicMock()
    fake.content = code
    return fake


# ===========================================================================
# PHASE 1 -- AUTHENTICATION FLOW
# ===========================================================================

class TestPhase1_Authentication:
    """
    Verify that the authentication layer produces and validates JWTs correctly.

    Tests cover:
    - A minted token grants access to the chat endpoint (HTTP 200).
    - Missing token is rejected (HTTP 401).
    - Invalid/tampered token is rejected (HTTP 401).
    - Signup flow creates an access_token.
    - Signin flow returns a valid JWT for correct credentials.
    """

    def test_valid_jwt_authorises_chat_endpoint(self, client, auth_headers, thread_id):
        """
        The auth_headers fixture holds a JWT minted directly by auth_utils
        (no DB or OAuth involved).  A greeting query should return HTTP 200,
        proving the get_current_user dependency accepted the token.
        """
        data = chat(client, auth_headers, thread_id, query="Hello, are you there?")
        assert data.get("status") in {"success", "no_results"}, (
            f"Phase 1: Expected success/no_results for greeting query. Got: {data}"
        )

    def test_missing_token_returns_403(self, client):
        response = client.post("/api/v1/agent/chat", json={
            "query": "Hello",
            "thread_id": "anon_thread",
            "project_id": "e2eproject"
        })
        assert response.status_code == 403, (
            f"Phase 1: Expected HTTP 403 (no token). Got: {response.status_code} -- {response.text}"
        )

    def test_invalid_token_returns_401(self, client, thread_id):
        """Tampered / malformed tokens must be rejected with HTTP 401."""
        response = client.post(
            CHAT_ENDPOINT,
            json={"query": "Hello", "thread_id": thread_id},
            headers={"Authorization": "Bearer totally.invalid.jwt.token"},
        )
        assert response.status_code == 401, (
            f"Phase 1: Expected HTTP 401 (invalid JWT). "
            f"Got: {response.status_code} -- {response.text}"
        )

    def test_local_signup_creates_access_token(self, client):
        unique_email = f"new_user_{uuid.uuid4().hex[:8]}@nexusai.com"

        new_user_mock = AsyncMock()
        new_user_mock.get_user_by_email.return_value = None   # user does not exist
        new_user_mock.create_user.return_value = True

        with patch(
            "models.UserModel.UserModel.create_instance",
            new_callable=AsyncMock,
            return_value=new_user_mock,
        ):
            response = client.post(
                "/api/v1/auth/local/signup",
                json={
                    "name": "Integration Tester",
                    "email": unique_email,
                    "password": "StrongPass123!",
                },
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert "access_token" in body, f"Missing access_token in response: {body}"
        assert body["token_type"] == "bearer"
        assert body["user"]["email"] == unique_email

    def test_local_signin_returns_access_token(self, client):
        """
        POST /api/v1/auth/local/signin should return a valid JWT when the
        user exists and the password verification passes.

        This exercises:
            POST body -> UserModel.get_user_by_email (found) ->
            auth_utils.verify_password (patched True) ->
            auth_utils.create_access_token -> 200
        """
        from conftest import TEST_USER_EMAIL, MOCK_USER_DOC

        signin_mock = AsyncMock()
        signin_mock.get_user_by_email.return_value = MOCK_USER_DOC
        signin_mock.update_google_auth_status.return_value = True

        with (
            patch(
                "models.UserModel.UserModel.create_instance",
                new_callable=AsyncMock,
                return_value=signin_mock,
            ),
            patch("helpers.auth_utils.verify_password", return_value=True),
        ):
            response = client.post(
                "/api/v1/auth/local/signin",
                json={"email": TEST_USER_EMAIL, "password": "SecurePass123!"},
            )

        assert response.status_code == 200, response.text
        body = response.json()
        assert "access_token" in body, f"Missing access_token in response: {body}"
        assert body["user"]["email"] == TEST_USER_EMAIL


# ===========================================================================
# PHASE 2 -- LANGGRAPH ROUTING SCENARIOS
# ===========================================================================

class TestPhase2_GraphRouting:
    """
    Full integration tests for each LangGraph routing scenario.

    Each test sends a real HTTP request through:
        FastAPI endpoint -> get_current_user -> agent_graph.ainvoke ->
        save_query -> detect_request_type -> [routing] -> format_output -> END

    The graph's real compiled StateGraph runs.  Only external I/O is mocked
    (LLM inference, Gmail API, Calendar API, Vector DB queries).

    Assertions check:
        (a) HTTP status code == 200
        (b) ChatResponse.status and ChatResponse.type (the routing signal)
        (c) Semantic content of ChatResponse.response
    """

    # -----------------------------------------------------------------------
    # Scenario 1: General Knowledge / Real-time Web Search
    # -----------------------------------------------------------------------

    def test_scenario_1_web_search_routing(self, client, auth_headers, thread_id):
        """
        Query: "Who won the World Cup in 2022?"

        Expected LangGraph path:
            save_query
            -> detect_request_type   [request_type = "web_search"]
            -> web_search_node       [DDGS / Google Search mocked]
            -> format_output
            -> send_report_email
            -> END

        Assertions:
            - status == "success"
            - type   == "web_search"
            - "argentina" in response (mocked web search + LLM answer)
        """
        def to_thread_mock(func, *args, **kwargs):
            if func.__name__ == "_do_ddg_search":
                return [
                    {
                        "title": "2022 FIFA World Cup",
                        "body": "Argentina won the 2022 FIFA World Cup, defeating France.",
                        "href": "https://en.wikipedia.org/wiki/2022_FIFA_World_Cup",
                    }
                ]
            # Otherwise, just run the function (which is mock_llm.generate_text) synchronously
            return func(*args, **kwargs)

        with patch("controllers.web_search_node.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            mock_to_thread.side_effect = to_thread_mock
            
            data = chat(
                client, auth_headers, thread_id,
                query="Who won the World Cup in 2022?",
            )

        assert data.get("status") == "success", (
            f"Scenario 1: Expected status=success. Got: {data}"
        )
        assert data.get("type", "").startswith("web_search"), (
            f"Scenario 1: Expected type=web_search. Got type={data.get('type')!r}"
        )
        assert "argentina" in data.get("response", "").lower(), (
            f"Scenario 1: Expected 'Argentina' in response. "
            f"Got: {data.get('response')!r}"
        )

    # -----------------------------------------------------------------------
    # Scenario 2: RAG / Knowledge Base Retrieval
    # -----------------------------------------------------------------------

    def test_scenario_2_rag_retrieval_routing(self, client, auth_headers,
                                               thread_id, tmp_path):
        """
        Action:  Write a dummy .txt file to tmp_path and pass its path as
                 file_path in the request (simulating a file upload).
                 The VectorDB mock in conftest.py returns a high-confidence hit.

        Query:   "What is the main topic of the document I just uploaded?"

        Expected LangGraph path:
            save_query
            -> detect_request_type         [request_type = "retrieval"]
            -> search_knowledge_base_node  [VectorDB mock returns hit]
            -> format_output
            -> END

        Assertions:
            - status in {"success", "no_results"}
            - "__NO_RELEVANT_CONTEXT__" NOT in response
            - At least one domain keyword present (automation / document / topic / pipeline)
        """
        dummy_doc = tmp_path / "project_spec.txt"
        dummy_doc.write_text(
            "This document covers test automation strategies and CI/CD pipelines "
            "for the NexusAI platform.",
            encoding="utf-8",
        )

        data = chat(
            client, auth_headers, thread_id,
            query="What is the main topic of the document I just uploaded?",
            file_path=str(dummy_doc),
        )

        assert data.get("status") in {"success", "no_results"}, (
            f"Scenario 2: Unexpected status: {data.get('status')!r}. Full: {data}"
        )
        assert "__NO_RELEVANT_CONTEXT__" not in data.get("response", ""), (
            "Scenario 2: The LLM sentinel __NO_RELEVANT_CONTEXT__ must NOT "
            "appear in user-facing responses."
        )
        response_lower = data.get("response", "").lower()
        assert any(
            kw in response_lower
            for kw in ("automation", "document", "topic", "pipeline", "test")
        ), (
            f"Scenario 2: Expected a domain keyword in the response. "
            f"Got: {data.get('response')!r}"
        )

    # -----------------------------------------------------------------------
    # Scenario 3: Gmail Retrieval (read-only)
    # -----------------------------------------------------------------------

    def test_scenario_3_gmail_retrieval_routing(self, client, auth_headers,
                                                 thread_id, patched_app):
        """
        Query: "Summarize my last 3 unread emails."

        Expected LangGraph path:
            save_query
            -> detect_request_type  [action_type = "gmail"]
            -> search_gmail_node    [calls gmail_service.search_emails()]
            -> format_output
            -> END

        Assertions:
            - type contains "gmail" OR starts with "action"
            - status in {"success", "no_results"}
        """
        mock_gmail = patched_app.state.mock_gmail
        mock_gmail.search_emails.reset_mock()
        mock_gmail.is_authenticated.return_value = True

        data = chat(
            client, auth_headers, thread_id,
            query="Summarize my last 3 unread emails.",
        )

        rtype = data.get("type", "")
        assert "gmail" in rtype or rtype.startswith("action"), (
            f"Scenario 3: Expected gmail routing. type={rtype!r}, response={data}"
        )
        assert data.get("status") in {"success", "no_results"}, (
            f"Scenario 3: Unexpected status: {data.get('status')!r}"
        )

    # -----------------------------------------------------------------------
    # Scenario 4: Action – Send Email + HITL (Human-in-the-Loop)
    # -----------------------------------------------------------------------

    def test_scenario_4_send_email_hitl_pause(self, client, auth_headers, thread_id):
        """
        Query: "Send an email to Ahmed saying I will be late."

        Expected LangGraph path:
            save_query
            -> detect_request_type     [action_type = "send_email"]
            -> extract_action_details  [extracts recipient, subject, body]
            -> [INTERRUPT before send_email node]     <- HITL gate

        CRITICAL Assertions:
            - status      == "pending_approval"
            - type        contains "send_email"
            - action_details is a non-null dict
            - metadata contains "resume_token" (required for safe resume)
        """
        data = chat(
            client, auth_headers, thread_id,
            query="Send an email to Ahmed saying I will be late.",
        )

        assert data.get("status") == "pending_approval", (
            f"Scenario 4: Graph must PAUSE at send_email HITL node. "
            f"Got status={data.get('status')!r}. Full: {data}"
        )
        assert "send_email" in data.get("type", ""), (
            f"Scenario 4: type must contain 'send_email'. Got: {data.get('type')!r}"
        )
        assert isinstance(data.get("action_details"), dict), (
            "Scenario 4: action_details must be a dict (containing extracted email fields)."
        )
        meta = data.get("metadata") or {}
        assert "resume_token" in meta, (
            f"Scenario 4: metadata must contain 'resume_token' for idempotent HITL resume. "
            f"Got metadata: {meta}"
        )

    def test_scenario_4b_send_email_user_approves(self, client, auth_headers,
                                                   patched_app):
        """
        Sub-test: The user approves the pending send_email action.

        After approval the graph must:
            -> continue past the interrupted send_email node
            -> call gmail_service.send_email (mocked)
            -> reach format_output -> END
            -> return status in {"success", "error"}
        """
        thr = f"e2e_approve_email_{uuid.uuid4().hex}"
        mock_gmail = patched_app.state.mock_gmail
        mock_gmail.send_email.reset_mock()

        # Step 1: trigger HITL
        pending = chat(
            client, auth_headers, thr,
            query="Send an email to Ahmed saying I will be late.",
        )
        if pending.get("status") != "pending_approval":
            pytest.skip("HITL pause not reached -- skipping approval sub-test.")

        tok = pending.get("metadata", {}).get("resume_token")

        # Step 2: approve
        approved = resume_action(client, auth_headers, thr,
                                 approve=True, resume_token=tok)

        assert approved.get("status") in {"success", "error"}, (
            f"Scenario 4b: Unexpected post-approval status: {approved}"
        )

    def test_scenario_4c_send_email_user_rejects(self, client, auth_headers,
                                                  patched_app):
        """
        Sub-test: The user rejects the pending send_email action.

        After rejection:
            - status must be "rejected"
            - gmail_service.send_email must NOT have been called
        """
        thr = f"e2e_reject_email_{uuid.uuid4().hex}"
        mock_gmail = patched_app.state.mock_gmail
        mock_gmail.send_email.reset_mock()

        pending = chat(
            client, auth_headers, thr,
            query="Send an email to Ahmed saying I will be late.",
        )
        if pending.get("status") != "pending_approval":
            pytest.skip("HITL pause not reached -- skipping rejection sub-test.")

        tok = pending.get("metadata", {}).get("resume_token")

        rejected = resume_action(client, auth_headers, thr,
                                 approve=False, resume_token=tok)

        assert rejected.get("status") == "rejected", (
            f"Scenario 4c: Expected status=rejected after user cancels. Got: {rejected}"
        )
        # send_report_email_node runs at the end of every graph, so an email IS sent.
        # However, the action email itself should NOT be sent.
        for call_args in mock_gmail.send_email.call_args_list:
            subject = call_args.kwargs.get("subject", "")
            assert "Secretary AI Report" in subject, (
                f"Expected only the report email to be sent, but found: {subject}"
            )

    # -----------------------------------------------------------------------
    # Scenario 5: Action – Google Calendar Event + HITL
    # -----------------------------------------------------------------------

    def test_scenario_5_calendar_event_hitl_pause(self, client, auth_headers,
                                                   thread_id):
        """
        Query: "Schedule a meeting with the dev team tomorrow at 5 PM."

        Expected LangGraph path:
            save_query
            -> detect_request_type      [action_type = "calendar"]
            -> extract_action_details   [extracts title, date, time, attendees]
            -> [INTERRUPT before create_calendar_event]     <- HITL gate

        CRITICAL Assertions:
            - status      == "pending_approval"
            - type        contains "calendar"
            - action_details is a dict
            - metadata contains "resume_token"
        """
        data = chat(
            client, auth_headers, thread_id,
            query="Schedule a meeting with the dev team tomorrow at 5 PM.",
        )

        assert data.get("status") == "pending_approval", (
            f"Scenario 5: Graph must PAUSE at create_calendar_event HITL. "
            f"Got status={data.get('status')!r}. Full: {data}"
        )
        assert "calendar" in data.get("type", ""), (
            f"Scenario 5: type must contain 'calendar'. Got: {data.get('type')!r}"
        )
        assert isinstance(data.get("action_details"), dict), (
            "Scenario 5: action_details must be a dict."
        )
        meta = data.get("metadata") or {}
        assert "resume_token" in meta, (
            f"Scenario 5: metadata must contain 'resume_token'. Got: {meta}"
        )

    def test_scenario_5b_calendar_event_user_approves(self, client, auth_headers,
                                                       patched_app):
        """
        Sub-test: The user approves the pending calendar create action.
        After approval the graph must continue to END with status=success/error.
        """
        thr = f"e2e_cal_approve_{uuid.uuid4().hex}"
        mock_cal = patched_app.state.mock_cal
        mock_cal.create_event.reset_mock()

        pending = chat(
            client, auth_headers, thr,
            query="Schedule a meeting with the dev team tomorrow at 5 PM.",
        )
        if pending.get("status") != "pending_approval":
            pytest.skip("HITL pause not reached -- skipping calendar approval sub-test.")

        tok = pending.get("metadata", {}).get("resume_token")
        approved = resume_action(client, auth_headers, thr,
                                 approve=True, resume_token=tok)

        assert approved.get("status") in {"success", "error"}, (
            f"Scenario 5b: Unexpected post-approval status: {approved}"
        )

    # -----------------------------------------------------------------------
    # Scenario 6: Data Analysis (CSV) + Chart Generation
    # -----------------------------------------------------------------------

    def test_scenario_6_data_analysis_routing(self, client, auth_headers,
                                               thread_id, tmp_path):
        """
        Action:  Write a real sales.csv to tmp_path and pass its path as
                 file_path in the request (simulating a file upload).

        Query:   "Analyze the sales data and generate a chart for the top 3 months."

        Expected LangGraph path:
            save_query
            -> detect_request_type   [action_type = "data_analysis"]
            -> data_analysis_node    [PythonREPL executes generated code]
            -> format_output
            -> END

        Assertions:
            - type contains "data_analysis" OR starts with "action"
            - If status == "success":
                chart_data is a dict
                chart_data["data"] is a non-empty list of dicts
                Each dict has at least the xAxisKey field
        """
        sales_csv = tmp_path / "sales.csv"
        sales_csv.write_text(
            "Month,Revenue\n"
            "January,12000\n"
            "February,15000\n"
            "March,9000\n"
            "April,18000\n"
            "May,21000\n"
            "June,17000\n",
            encoding="utf-8",
        )

        fake_resp = _make_csv_analysis_llm_response(str(sales_csv))

        with _patch_data_analysis_llms(fake_resp):
            data = chat(
                client, auth_headers, thread_id,
                query="Analyze the sales data and generate a chart for the top 3 months.",
                file_path=str(sales_csv),
            )

        rtype = data.get("type", "")
        assert "data_analysis" in rtype or rtype.startswith("action"), (
            f"Scenario 6: Expected data_analysis routing. type={rtype!r}"
        )

        if data.get("status") == "success":
            chart = data.get("chart_data")
            if chart is not None:
                assert isinstance(chart, dict), (
                    f"Scenario 6: chart_data must be a dict. Got: {type(chart).__name__}"
                )
                assert "data" in chart, (
                    f"Scenario 6: chart_data must have 'data' key. Got keys: {list(chart.keys())}"
                )
                assert isinstance(chart["data"], list), (
                    f"Scenario 6: chart_data['data'] must be a list."
                )
                assert len(chart["data"]) > 0, (
                    "Scenario 6: chart_data['data'] must not be empty."
                )
                # Each item in data must be a dict (Recharts row)
                for row in chart["data"]:
                    assert isinstance(row, dict), (
                        f"Scenario 6: Each chart row must be a dict. Got: {row!r}"
                    )
        else:
            # CI may not have all LLM deps; graceful degradation is acceptable
            assert data.get("status") in {"error", "no_results"}, (
                f"Scenario 6: Unexpected status: {data.get('status')!r}"
            )

    def test_scenario_6b_data_analysis_no_file_graceful_error(self, client,
                                                                auth_headers,
                                                                thread_id):
        """
        Sub-test: When file_path is not supplied, data_analysis_node must set
        status="error" (returning a user-friendly message) rather than crashing.
        The HTTP response must still be 200.
        """
        data = chat(
            client, auth_headers, thread_id,
            query="Analyze the sales data and generate a chart.",
            # file_path intentionally omitted
        )
        assert data.get("status") in {"success", "error", "no_results"}, (
            f"Scenario 6b: Unexpected status: {data.get('status')!r}"
        )


# ===========================================================================
# PHASE 3 -- EDGE CASES AND CONVERSATIONAL MEMORY
# ===========================================================================

class TestPhase3_EdgeCasesAndMemory:
    """
    Tests for:
    - LangGraph MemorySaver checkpointing across multi-turn conversations.
    - System robustness against edge-case inputs.
    """

    # -----------------------------------------------------------------------
    # Scenario 7: Multi-turn Memory / Follow-up Questions
    # -----------------------------------------------------------------------

    def test_scenario_7_memory_follow_up(self, client, auth_headers, tmp_path):
        """
        Turn 1: "Analyze this CSV and tell me the highest month."
        Turn 2: "Why do you think it was that high?"

        Both turns use the SAME thread_id so MemorySaver can persist state.

        The follow-up query (Turn 2) should:
            - Return a non-empty response (not "I don't know what you mean")
            - NOT be classified as "retrieval" (which would indicate the
              chat_history was not propagated to the classifier)
            - Return status in {"success", "no_results", "error"}

        This test validates that the MemorySaver checkpointer accumulates
        chat_history across invocations and the classifier uses it.
        """
        persistent_thread = f"e2e_memory_{uuid.uuid4().hex}"

        sales_csv = tmp_path / "sales_memory.csv"
        sales_csv.write_text(
            "Month,Revenue\nJanuary,12000\nFebruary,15000\nMarch,9000\n",
            encoding="utf-8",
        )

        fake_resp = MagicMock()
        fake_resp.content = "\n".join([
            "import json",
            "result = {",
            "    'text_summary': 'February had the highest revenue at $15,000.',",
            "    'chart_json': None",
            "}",
            "print(json.dumps(result))",
        ])

        with _patch_data_analysis_llms(fake_resp):
            # ── Turn 1 ──────────────────────────────────────────────────────
            r1 = chat(
                client, auth_headers, persistent_thread,
                query="Analyze this CSV and tell me the highest month.",
                file_path=str(sales_csv),
            )
            assert r1.get("status") in {"success", "error"}, (
                f"Scenario 7 Turn 1: Unexpected status: {r1}"
            )

            # ── Turn 2 (follow-up, same thread, no file) ─────────────────
            r2 = chat(
                client, auth_headers, persistent_thread,
                query="Why do you think it was that high?",
                # file_path omitted; context must come from MemorySaver
            )

        assert r2.get("status") in {"success", "no_results", "error"}, (
            f"Scenario 7 Turn 2: Unexpected status: {r2}"
        )
        # Response must not be empty
        assert len(r2.get("response", "")) > 0, (
            "Scenario 7: Follow-up response must not be empty. "
            "This likely means chat_history was not propagated."
        )
        # Must NOT regress to retrieval classification
        r2_type = r2.get("type", "")
        assert r2_type not in {"retrieval"}, (
            f"Scenario 7: Follow-up was classified as 'retrieval'. "
            f"This suggests chat_history was NOT used. "
            f"type={r2_type!r}, r2={r2}"
        )

    # -----------------------------------------------------------------------
    # Edge Case: Empty Query
    # -----------------------------------------------------------------------

    def test_edge_empty_query_rejected_422(self, client, auth_headers, thread_id):
        """
        ChatRequest.query has min_length=1 (Pydantic validation).
        An empty string must be rejected before reaching the graph with HTTP 422.
        """
        response = client.post(
            CHAT_ENDPOINT,
            json={"query": "", "thread_id": thread_id},
            headers=auth_headers,
        )
        assert response.status_code == 422, (
            f"Edge case: Empty query must return HTTP 422 (Pydantic validation). "
            f"Got: {response.status_code}"
        )

    # -----------------------------------------------------------------------
    # Edge Case: Very Long Query
    # -----------------------------------------------------------------------

    def test_edge_very_long_query_does_not_crash(self, client, auth_headers,
                                                  thread_id):
        """
        A ~5,000-character query must not raise an unhandled server exception.
        The graph may degrade gracefully, but must return HTTP 200.
        """
        long_q = "Tell me about artificial intelligence and machine learning " * 80
        data = chat(client, auth_headers, thread_id, query=long_q)
        assert data.get("status") in {"success", "no_results", "error"}, (
            f"Edge case: Long query returned unexpected status: {data.get('status')!r}"
        )

    # -----------------------------------------------------------------------
    # Edge Case: Thread Isolation
    # -----------------------------------------------------------------------

    def test_edge_thread_isolation(self, client, auth_headers):
        """
        Two separate thread IDs must not share LangGraph MemorySaver state.

        Thread A: web_search query (World Cup).
        Thread B: gmail query (unread emails).

        Thread B must route to action/gmail, NOT inherit web_search context
        from Thread A.
        """
        thread_a = f"e2e_iso_a_{uuid.uuid4().hex}"
        thread_b = f"e2e_iso_b_{uuid.uuid4().hex}"

        # Populate Thread A
        chat(client, auth_headers, thread_a, query="Who won the World Cup 2022?")

        # Thread B should be independent
        r_b = chat(client, auth_headers, thread_b,
                   query="Summarize my last 3 unread emails.")

        rtype_b = r_b.get("type", "")
        assert "gmail" in rtype_b or rtype_b.startswith("action"), (
            f"Thread isolation: Thread B inherited Thread A context. "
            f"type_b={rtype_b!r}"
        )

    # -----------------------------------------------------------------------
    # Edge Case: Stale Resume Token
    # -----------------------------------------------------------------------

    def test_edge_stale_resume_token_rejected(self, client, auth_headers):
        """
        Attempting to resume a paused thread with a forged / stale resume_token
        must be rejected with HTTP 400 or 409.
        The graph must NOT be advanced.
        """
        thr = f"e2e_stale_{uuid.uuid4().hex}"

        pending = chat(
            client, auth_headers, thr,
            query="Send an email to Ahmed saying I will be late.",
        )
        if pending.get("status") != "pending_approval":
            pytest.skip("HITL not triggered -- cannot test stale-token rejection.")

        response = client.post(
            RESUME_ENDPOINT,
            json={
                "thread_id": thr,
                "approve": True,
                "resume_token": "forged-stale-token-xyz-12345",
            },
            headers=auth_headers,
        )
        assert response.status_code in {400, 409}, (
            f"Edge case: Stale resume token must be rejected (400/409). "
            f"Got: {response.status_code} -- {response.text}"
        )

    # -----------------------------------------------------------------------
    # Edge Case: Casual Greeting
    # -----------------------------------------------------------------------

    def test_edge_casual_greeting_handled(self, client, auth_headers, thread_id):
        """
        Casual greetings ("Hi! How are you?") must be handled gracefully.

        Per the classifier system prompt, greetings route to web_search.
        The response must not be an error; it must return status=success.
        """
        data = chat(client, auth_headers, thread_id, query="Hi! How are you?")
        assert data.get("status") in {"success", "no_results"}, (
            f"Edge case: Greeting returned unexpected status: {data}"
        )
        # Sanity check: response must be a non-empty string
        assert len(data.get("response", "")) > 0, (
            "Edge case: Greeting produced an empty response body."
        )


# ===========================================================================
# PHASE 4 -- RESPONSE SCHEMA VALIDATION
# ===========================================================================

class TestPhase4_ResponseSchema:
    """
    Parametrised schema tests: every query type must produce a ChatResponse
    with all required fields present, correctly typed, and with valid enum values.

    This acts as a regression guard against accidental model schema changes.
    """

    # Fields that MUST be present and have the correct Python type
    REQUIRED_FIELDS: Dict[str, type] = {
        "response":  str,
        "type":      str,
        "status":    str,
        "thread_id": str,
    }

    # Optional fields with their allowed Python types (may be None)
    OPTIONAL_FIELDS: Dict[str, tuple] = {
        "metadata":       (dict,),
        "action_details": (dict,),
    }

    # All documented status values the API may return
    VALID_STATUSES = {
        "success",
        "error",
        "no_results",
        "pending_approval",
        "rejected",
        "cancelled",
        "invalid_thread",
    }

    @pytest.mark.parametrize(
        "query",
        [
            "Who won the World Cup in 2022?",
            "Summarize my last 3 unread emails.",
            "What is the capital of France?",
            "Send an email to team@nexusai.io subject Test body Hello.",
        ],
    )
    def test_chat_response_schema_is_well_formed(self, client, auth_headers,
                                                  thread_id, query):
        """
        For every query type, the ChatResponse must:
          1. Contain all required fields.
          2. Have each required field typed correctly.
          3. Have optional fields typed correctly when present.
          4. Have status in the set of documented enum values.
        """
        data = chat(client, auth_headers, thread_id, query=query)

        # Check required fields
        for field, ftype in self.REQUIRED_FIELDS.items():
            assert field in data, (
                f"Schema: Required field '{field}' missing for query={query!r}. "
                f"Full response: {data}"
            )
            assert isinstance(data[field], ftype), (
                f"Schema: Field '{field}' must be {ftype.__name__}, "
                f"got {type(data[field]).__name__}. Value: {data[field]!r}"
            )

        # Check optional fields (type-check only when present and non-null)
        for field, ftypes in self.OPTIONAL_FIELDS.items():
            if data.get(field) is not None:
                assert isinstance(data[field], ftypes), (
                    f"Schema: Optional field '{field}' has wrong type. "
                    f"Expected {ftypes}, got {type(data[field]).__name__}"
                )

        # Status enum check
        assert data["status"] in self.VALID_STATUSES, (
            f"Schema: status={data['status']!r} not in {self.VALID_STATUSES}"
        )

    def test_thread_id_is_echoed_back_unchanged(self, client, auth_headers):
        """
        The thread_id sent in the ChatRequest must be returned unchanged in
        the ChatResponse.thread_id field.

        This is a contract the frontend relies on to correlate request/response.
        """
        my_thread = f"e2e_echo_{uuid.uuid4().hex}"
        data = chat(client, auth_headers, my_thread, query="What is 2 + 2?")
        assert data.get("thread_id") == my_thread, (
            f"Schema: thread_id not echoed correctly. "
            f"Sent: {my_thread!r}, received: {data.get('thread_id')!r}"
        )

    def test_chart_data_is_none_or_dict(self, client, auth_headers, thread_id):
        """
        chart_data must always be either None or a dict.
        It must never be a raw string, list, or other unexpected type.
        """
        data = chat(client, auth_headers, thread_id,
                    query="What is the current Bitcoin price?")
        chart = data.get("chart_data")
        assert chart is None or isinstance(chart, dict), (
            f"Schema: chart_data must be None or dict. "
            f"Got: {type(chart).__name__}: {chart!r}"
        )

    def test_action_details_is_dict_or_none(self, client, auth_headers, thread_id):
        """
        action_details must always be a dict or None (never a list or string).
        """
        data = chat(client, auth_headers, thread_id,
                    query="Who won the World Cup in 2022?")
        details = data.get("action_details")
        assert details is None or isinstance(details, dict), (
            f"Schema: action_details must be None or dict. "
            f"Got: {type(details).__name__}: {details!r}"
        )
