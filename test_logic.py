import requests
import uuid
import sys
sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = "http://127.0.0.1:5000/api/v1"

def ask_agent(token, query):
    headers = {"Authorization": f"Bearer {token}"}
    print(f"\n--- QUERY: {query} ---")
    payload = {"query": query, "thread_id": "logic_test_thread", "project_id": "default"}
    res = requests.post(f"{BASE_URL}/agent/chat", json=payload, headers=headers)
    
    if res.status_code == 200:
        data = res.json()
        print(f"Classification Type: {data.get('type')}")
        print(f"Response: {data.get('response')}")
        if data.get('action_details'):
            print(f"Action Details: {data.get('action_details')}")
    else:
        print(f"FAILED {res.status_code}: {res.text}")

def test_logic():
    print("=== Logic Testing ===")
    
    # 1. Setup User & Token
    unique_email = f"logic_{uuid.uuid4().hex[:8]}@example.com"
    password = "StrongPassword123!"
    requests.post(f"{BASE_URL}/auth/local/signup", json={"email": unique_email, "password": password, "name": "Logic User"})
    login = requests.post(f"{BASE_URL}/auth/local/signin", json={"email": unique_email, "password": password}).json()
    token = login["access_token"]
    
    # Run tests
    queries = [
        "Who is the current CEO of Apple?",
        "What is the price of Bitcoin today?",
        "What are my latest emails?",
        "give me the head and content of the last 5 mails",
        "Send an email to ahmed@example.com saying I will be late.",
        "What meetings do I have tomorrow?",
        "Schedule a meeting with the team next Thursday at 5 PM."
    ]
    for q in queries:
        ask_agent(token, q)

if __name__ == "__main__":
    test_logic()
