# -*- coding: utf-8 -*-
"""
Comprehensive routing test for the Supervisor/Classifier Node.
Tests all 6 routing scenarios defined in the task requirements.

Run from the src/ directory:
    cd e:/DEPI/Project/src
    python -X utf8 test_classifier_routing.py
"""

import sys
import os
import time

# Force UTF-8 output on Windows (avoids cp1252 UnicodeEncodeError on Arabic text)
if sys.stdout.encoding != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from controllers.classifier import ClassifierController


# ── Helper: map (request_type, action_type) → node name ──────────────────────
def classify_to_node(req_type: str, act_type: str) -> str:
    """Mirror of route_request_type logic in agent_graph.py."""
    if req_type == "conversational":
        return "conversational_node"
    if req_type == "web_search":
        return "web_search"
    if act_type == "data_analysis":
        return "data_analysis"
    if act_type == "summary":
        return "daily_summary"
    if act_type == "gmail":
        return "search_gmail"
    if req_type == "action":
        return "extract_action_details"
    return "search_knowledge_base"  # retrieval default


# ── Test cases ─────────────────────────────────────────────────────────────────
TEST_CASES = [
    {
        "id": 1,
        "query": "ازيك عامل ايه",
        "expected_request_type": "conversational",
        "expected_action_type": "",
        "expected_node": "conversational_node",
        "description": "Casual chit-chat / greeting",
    },
    {
        "id": 2,
        "query": "analyze the file I uploaded",
        "expected_request_type": "action",
        "expected_action_type": "data_analysis",
        "expected_node": "data_analysis",
        "description": "CSV/Data analysis request",
    },
    {
        "id": 3,
        "query": "search for emails from Mohamed",
        "expected_request_type": "action",
        "expected_action_type": "gmail",
        "expected_node": "search_gmail",
        "description": "Gmail search request",
    },
    {
        "id": 4,
        "query": "send an email to Ahmed telling him I will be late",
        "expected_request_type": "action",
        "expected_action_type": "send_email",
        "expected_node": "extract_action_details",
        "description": "Send email request",
    },
    {
        "id": 5,
        "query": "book a meeting for me tomorrow at 5pm",
        "expected_request_type": "action",
        "expected_action_type": "calendar",
        "expected_node": "extract_action_details",
        "description": "Calendar booking request",
    },
    {
        "id": 6,
        "query": "what emails did I receive today?",
        "expected_request_type": "action",
        "expected_action_type": "gmail",
        "expected_node": "search_gmail",
        "description": "Today's incoming emails (read/summarize)",
    },
]


def run_tests(generation_client):
    classifier = ClassifierController(generation_client=generation_client)
    results = []

    SEP = "=" * 90
    print("\n" + SEP)
    print(f"{'ID':<4} {'Query':<40} {'req / act':<32} {'Node':<25} Status")
    print(SEP)

    for tc in TEST_CASES:
        # Retry up to 2 times for transient empty responses
        classification = {}
        for attempt in range(2):
            classification = classifier.classify_query(query=tc["query"], chat_history=[])
            if classification.get("request_type"):
                break
            time.sleep(1)

        actual_req = classification.get("request_type", "")
        actual_act = classification.get("action_type", "")
        actual_node = classify_to_node(actual_req, actual_act)

        req_ok = actual_req == tc["expected_request_type"]
        act_ok = actual_act == tc["expected_action_type"]
        node_ok = actual_node == tc["expected_node"]
        passed = req_ok and act_ok and node_ok
        status = "PASS" if passed else "FAIL"

        short_q = tc["query"][:37] + "..." if len(tc["query"]) > 37 else tc["query"]
        got_label = f"{actual_req} / {actual_act}"
        print(f"{tc['id']:<4} {short_q:<40} {got_label:<32} {actual_node:<25} [{status}]")

        if not passed:
            exp_label = f"{tc['expected_request_type']} / {tc['expected_action_type']}"
            print(f"     Expected: {exp_label:<32} -> {tc['expected_node']}")

        results.append({
            "id": tc["id"],
            "description": tc["description"],
            "query": tc["query"],
            "expected_req": tc["expected_request_type"],
            "expected_act": tc["expected_action_type"],
            "expected_node": tc["expected_node"],
            "actual_req": actual_req,
            "actual_act": actual_act,
            "actual_node": actual_node,
            "passed": passed,
        })

    print(SEP)
    passed_count = sum(1 for r in results if r["passed"])
    print(f"\nResult: {passed_count}/{len(results)} tests passed")

    # --- Final Summary Table ---
    print("\n--- DETAILED RESULTS ---")
    header = f"{'#':<3} {'Description':<35} {'Expected Node':<28} {'Actual Node':<28} {'Status'}"
    print(header)
    print("-" * len(header))
    for r in results:
        s = "OK" if r["passed"] else "FAIL"
        print(f"{r['id']:<3} {r['description']:<35} {r['expected_node']:<28} {r['actual_node']:<28} {s}")

    return results


# ── Bootstrap generation client ───────────────────────────────────────────────
if __name__ == "__main__":
    try:
        from stores.llm.LLMProviderFactory import LLMProviderFactory
        from helpers.config import get_settings

        settings = get_settings()
        factory = LLMProviderFactory(config=settings)
        generation_client = factory.create(provider=settings.GENERATION_BACKEND)

        # CRITICAL: must call set_generation_model — same as main.py startup does
        generation_client.set_generation_model(model_id=settings.GENERATION_MODEL_ID)

        print(f"[Setup] LLM backend : {settings.GENERATION_BACKEND}")
        print(f"[Setup] Model       : {settings.GENERATION_MODEL_ID}")
    except Exception as e:
        print(f"[Setup ERROR] {e}")
        sys.exit(1)

    run_tests(generation_client)
