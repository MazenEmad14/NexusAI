import requests

BASE_URL = "http://localhost:5000/api/v1"

print("="*50)
print("  EXHAUSTIVE TESTING SCRIPT")
print("="*50)

def print_result(case_name, res, expected_status=200):
    status = getattr(res, "status_code", None)
    if status is None:
        print(f"[FAIL] {case_name} -> FAILED (No response)")
        return False
    if status == expected_status or (expected_status == 200 and status in [200, 201]):
        print(f"[PASS] {case_name} -> PASS ({status})")
        return True
    else:
        print(f"[FAIL] {case_name} -> FAIL (Expected {expected_status}, got {status})")
        try:
            print("   Response:", res.json())
        except:
            print("   Response:", res.text[:200])
        return False

def test_auth():
    print("\n--- Testing Auth ---")
    try:
        r = requests.get(f"{BASE_URL}/auth/google/login")
        print_result("Auth Login Redirect", r, 200)
    except Exception as e:
        print(f"[FAIL] Auth Login Exception: {e}")
    try:
        r = requests.get(f"{BASE_URL}/auth/google/status")
        print_result("Auth Status Check (No token)", r, 200)
    except Exception as e:
        print(f"[FAIL] Auth Status Exception: {e}")

def test_data():
    print("\n--- Testing Data ---")
    files = {"file": ("test_doc.txt", b"Hello world, this is a test document for QA.", "text/plain")}
    try:
        r = requests.post(f"{BASE_URL}/data/upload/default", files=files)
        print_result("Upload TXT File (Happy Path)", r, 200)
        file_id = r.json().get("file_id") if r.status_code == 200 else None
    except Exception as e:
        print(f"[FAIL] Upload Happy Path Exception: {e}")
        file_id = None

    if file_id:
        try:
            r = requests.post(f"{BASE_URL}/data/process/default", json={"file_id": file_id, "chunk_size": 100, "overlap_size": 10, "do_reset": False})
            print_result("Process File (Happy Path)", r, 200)
        except Exception as e:
            print(f"[FAIL] Process Happy Path Exception: {e}")

    files_invalid = {"file": ("test.exe", b"fake exe data", "application/x-msdownload")}
    try:
        r = requests.post(f"{BASE_URL}/data/upload/default", files=files_invalid)
        print_result("Upload Invalid File Type (Edge)", r, 400)
    except Exception as e:
        print(f"[FAIL] Upload Invalid File Exception: {e}")

    try:
        r = requests.post(f"{BASE_URL}/data/process/default", json={"file_id": file_id, "chunk_size": -100, "overlap_size": 10, "do_reset": False})
        print_result("Process Negative Chunk Size (Edge)", r, 422)
    except Exception as e:
        print(f"[FAIL] Process Negative Chunk Exception: {e}")

def test_nlp():
    print("\n--- Testing NLP ---")
    try:
        r = requests.post(f"{BASE_URL}/nlp/index/push/default", json={"do_reset": False})
        print_result("Push Index (Happy Path)", r, 200)
    except Exception as e:
        print(f"[FAIL] Push Index Exception: {e}")
    try:
        r = requests.post(f"{BASE_URL}/nlp/index/search/default", json={"text": "QA", "limit": 2})
        print_result("Search Index (Happy Path)", r, 200)
    except Exception as e:
        print(f"[FAIL] Search Index Exception: {e}")
    try:
        r = requests.post(f"{BASE_URL}/nlp/index/search/default", json={"text": "", "limit": -5})
        print_result("Search Invalid Limit (Boundary)", r, 422)
    except Exception as e:
        print(f"[FAIL] Search Invalid Limit Exception: {e}")

def test_dashboard():
    print("\n--- Testing Dashboard ---")
    endpoints = [
        ("/tasks?project_id=default", 200, "Tasks"),
        ("/reminders?project_id=default", 200, "Reminders"),
        ("/calendar/events", 200, "Calendar Events (No Auth)"),
        ("/summary/daily?project_id=default", 200, "Daily Summary"),
        ("/memory/test_thread", 200, "Thread memory"),
    ]
    for path, exp_code, label in endpoints:
        try:
            r = requests.get(f"{BASE_URL}{path}", timeout=30)
            print_result(f"Dashboard {label}", r, exp_code)
        except Exception as e:
            print(f"[FAIL] Dashboard {label} Exception: {e}")

def test_agent():
    print("\n--- Testing Agent Chat ---")
    try:
        r = requests.post(f"{BASE_URL}/agent/chat", json={"query": "Hello AI!", "project_id": "default", "thread_id": "advanced_test_thread"}, timeout=30)
        print_result("Agent Chat Basic (Happy Path)", r, 200)
    except Exception as e:
        print(f"[FAIL] Agent Chat Exception: {e}")

    try:
        r = requests.post(f"{BASE_URL}/agent/chat", json={"query": "Hello AI!", "project_id": "default"}, timeout=30)
        print_result("Agent Chat Missing Thread ID", r, 422)
    except Exception as e:
        print(f"[FAIL] Agent Chat Missing Thread ID Exception: {e}")

if __name__ == "__main__":
    test_auth()
    test_data()
    test_nlp()
    test_dashboard()
    test_agent()
    print("\nDone.")
