"""
Full Operational Audit - API Endpoint Tests
Tests all critical endpoints for the Secretary AI Agent project.
"""
import sys
import os
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout.reconfigure(encoding="utf-8")

import requests
import json

BASE = "http://localhost:5000"


def test_endpoint(method, path, label, body=None, timeout=10):
    print(f"\n{'='*60}")
    print(f"  {method} {path}")
    print(f"  Label: {label}")
    print(f"{'='*60}")
    try:
        if method == "GET":
            r = requests.get(f"{BASE}{path}", timeout=timeout)
        elif method == "POST":
            r = requests.post(
                f"{BASE}{path}",
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=timeout,
            )
        else:
            print(f"  SKIP: unsupported method {method}")
            return None

        print(f"  Status Code: {r.status_code}")
        try:
            data = r.json()
            print(f"  Response: {json.dumps(data, indent=2, default=str)[:1200]}")
            return data
        except Exception:
            print(f"  Response (text): {r.text[:500]}")
            return r.text
    except requests.exceptions.ConnectionError:
        print("  FAIL: CONNECTION REFUSED - backend not reachable")
        return None
    except requests.exceptions.Timeout:
        print(f"  FAIL: TIMEOUT after {timeout}s")
        return None
    except Exception as e:
        print(f"  FAIL: {type(e).__name__}: {e}")
        return None


def main():
    print("=" * 60)
    print("  SECRETARY AI AGENT - FULL API AUDIT")
    print("=" * 60)

    # --- STEP 3: Service Health ---
    print("\n\n" + "=" * 60)
    print("  STEP 3: SERVICE CONNECTIONS")
    print("=" * 60)

    test_endpoint("GET", "/api/v1/", "Base welcome endpoint")
    health = test_endpoint("GET", "/api/v1/health/services", "Service health check")

    if health:
        data = health.get("data", {})
        print("\n  SERVICE STATUS SUMMARY:")
        print(f"    MongoDB:  {data.get('database', 'unknown')}")
        print(f"    Gmail:    {data.get('gmail', 'unknown')}")
        print(f"    Calendar: {data.get('calendar', 'unknown')}")

    # --- STEP 4: API Routes ---
    print("\n\n" + "=" * 60)
    print("  STEP 4: API ROUTE TESTS")
    print("=" * 60)

    # Dashboard endpoints
    test_endpoint("GET", "/api/v1/tasks?project_id=default", "Fetch tasks")
    test_endpoint("GET", "/api/v1/reminders?project_id=default", "Fetch reminders")
    test_endpoint("GET", "/api/v1/calendar/events", "Fetch calendar events")
    test_endpoint("GET", "/api/v1/summary/daily?project_id=default", "Daily summary", timeout=30)
    test_endpoint("GET", "/api/v1/memory/test_thread", "Thread memory")

    # Agent chat
    print("\n\n" + "=" * 60)
    print("  AGENT CHAT TEST")
    print("=" * 60)

    chat_result = test_endpoint(
        "POST",
        "/api/v1/agent/chat",
        "Agent chat - retrieval query",
        body={
            "query": "What tasks do I have pending?",
            "project_id": "default",
            "thread_id": "audit_test_thread_1",
        },
        timeout=30,
    )

    if chat_result:
        print("\n  CHAT RESPONSE ANALYSIS:")
        print(f"    response: {str(chat_result.get('response', ''))[:200]}")
        print(f"    type:     {chat_result.get('type', '')}")
        print(f"    status:   {chat_result.get('status', '')}")
        print(f"    thread:   {chat_result.get('thread_id', '')}")

    # Agent chat - action query (reminder)
    chat_action = test_endpoint(
        "POST",
        "/api/v1/agent/chat",
        "Agent chat - action query (set reminder)",
        body={
            "query": "Remind me to review the project report tomorrow at 10am",
            "project_id": "default",
            "thread_id": "audit_test_thread_2",
        },
        timeout=30,
    )

    if chat_action:
        print("\n  ACTION RESPONSE ANALYSIS:")
        print(f"    response: {str(chat_action.get('response', ''))[:200]}")
        print(f"    type:     {chat_action.get('type', '')}")
        print(f"    status:   {chat_action.get('status', '')}")
        print(f"    thread:   {chat_action.get('thread_id', '')}")
        print(f"    details:  {chat_action.get('action_details', {})}")

    # Resume endpoint test (if pending)
    if chat_action and chat_action.get("status") == "pending_approval":
        print("\n  HITL approval detected - testing resume...")
        resume_result = test_endpoint(
            "POST",
            "/api/v1/agent/resume",
            "Resume (approve reminder)",
            body={
                "thread_id": chat_action.get("thread_id", "audit_test_thread_2"),
                "approve": True,
            },
            timeout=30,
        )
        if resume_result:
            print("\n  RESUME RESPONSE:")
            print(f"    response: {str(resume_result.get('response', ''))[:200]}")
            print(f"    status:   {resume_result.get('status', '')}")

    # Resume on non-existent thread
    test_endpoint(
        "POST",
        "/api/v1/agent/resume",
        "Resume - non-existent thread (expect error)",
        body={
            "thread_id": "nonexistent_thread_xyz",
            "approve": True,
        },
        timeout=10,
    )

    # MongoDB verify - check data after operations
    print("\n\n" + "=" * 60)
    print("  MONGODB DATA VERIFICATION")
    print("=" * 60)

    tasks_after = test_endpoint("GET", "/api/v1/tasks?project_id=default", "Tasks after operations")
    reminders_after = test_endpoint("GET", "/api/v1/reminders?project_id=default", "Reminders after operations")

    if tasks_after:
        tasks = tasks_after.get("data", {}).get("tasks", [])
        print(f"\n  Tasks in DB: {len(tasks)}")
        for t in tasks[:3]:
            print(f"    - {t.get('description', 'N/A')} | status={t.get('status', 'N/A')} | deadline={t.get('deadline', 'N/A')}")

    if reminders_after:
        reminders = reminders_after.get("data", {}).get("reminders", [])
        print(f"\n  Reminders in DB: {len(reminders)}")
        for r in reminders[:3]:
            print(f"    - {r.get('task', 'N/A')} | date={r.get('date', 'N/A')} | time={r.get('time', 'N/A')}")

    # Vector DB info
    test_endpoint("GET", "/api/v1/nlp/index/info/default", "Vector DB collection info")

    print("\n\n" + "=" * 60)
    print("  AUDIT COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
