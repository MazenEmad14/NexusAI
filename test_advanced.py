import requests
import uuid
import time
import sys
import io

# Fix unicode print issues on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

base_url = "http://localhost:5000/api/v1"

def test_origin_protection():
    print("\n--- Test 14: Origin Protection ---")
    
    # Evil origin
    print("Testing with origin=https://evil.com")
    res1 = requests.get(f"{base_url}/auth/google/login?origin=https://evil.com")
    print(f"Status: {res1.status_code}")
    print(f"Response URL contains evil.com? {'evil.com' in res1.json().get('auth_url', '')}")

def test_tenant_isolation():
    print("\n--- Test 15 & 16: Tenant Isolation ---")
    
    userA_email = f"userA_{uuid.uuid4().hex[:8]}@example.com"
    userB_email = f"userB_{uuid.uuid4().hex[:8]}@example.com"
    password = "StrongPassword123!"

    print("1. Creating UserA...")
    resA = requests.post(f"{base_url}/auth/local/signup", json={"name": "UserA", "email": userA_email, "password": password})
    tokenA = resA.json()["access_token"]

    print("2. Creating UserB...")
    resB = requests.post(f"{base_url}/auth/local/signup", json={"name": "UserB", "email": userB_email, "password": password})
    tokenB = resB.json()["access_token"]

    headersA = {"Authorization": f"Bearer {tokenA}"}
    headersB = {"Authorization": f"Bearer {tokenB}"}

    print("\n3. UserA creates a thread and says a secret...")
    thread_id = f"test_thread_{uuid.uuid4().hex[:8]}"
    
    chat_payload_1 = {
        "query": "My secret code is 998877. Just remember it and say 'OK'.",
        "project_id": "default",
        "thread_id": thread_id
    }
    chat_res = requests.post(f"{base_url}/agent/chat", json=chat_payload_1, headers=headersA)
    print(f"UserA Chat 1 Status: {chat_res.status_code}")
    
    print("\n4. UserB logs in and queries the EXACT SAME thread ID...")
    chat_payload_2 = {
        "query": "What is my secret code? If you don't know, say 'I dont know'.",
        "project_id": "default",
        "thread_id": thread_id
    }
    chat_res_B = requests.post(f"{base_url}/agent/chat", json=chat_payload_2, headers=headersB)
    print(f"UserB Chat Status: {chat_res_B.status_code}")
    ans = chat_res_B.json().get("response", "")
    print(f"UserB Chat Response: {ans}")
    print(f"Did UserB get the secret? {'998877' in ans}")

if __name__ == "__main__":
    test_origin_protection()
    test_tenant_isolation()
