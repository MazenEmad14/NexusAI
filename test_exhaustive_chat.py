import requests
import uuid
import asyncio
import httpx

BASE_URL = "http://127.0.0.1:5000/api/v1"
errors = []

async def test_chat_exhaustive():
    print("=== Exhaustive Chat Testing ===")
    
    # 1. Setup User & Token
    unique_email = f"chat_tester_{uuid.uuid4().hex[:8]}@example.com"
    password = "StrongPassword123!"
    requests.post(f"{BASE_URL}/auth/local/signup", json={"email": unique_email, "password": password, "name": "Chat User"})
    login = requests.post(f"{BASE_URL}/auth/local/signin", json={"email": unique_email, "password": password}).json()
    token = login["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Happy Path
    chat_payload = {"query": "Hello AI", "thread_id": "thread_1", "project_id": "default"}
    res = requests.post(f"{BASE_URL}/agent/chat", json=chat_payload, headers=headers)
    if res.status_code != 200:
        errors.append({"case": "Happy Path Chat", "error": f"Failed with {res.status_code}: {res.text}"})

    # 3. Edge Case: Empty String
    res = requests.post(f"{BASE_URL}/agent/chat", json={"query": "", "thread_id": "thread_1", "project_id": "default"}, headers=headers)
    if res.status_code != 422:
        errors.append({"case": "Empty String", "error": f"Expected 422, got {res.status_code}"})

    # 4. Edge Case: Extremely Long String
    res = requests.post(f"{BASE_URL}/agent/chat", json={"query": "A" * 10000, "thread_id": "thread_1", "project_id": "default"}, headers=headers)
    if res.status_code == 500:
        errors.append({"case": "Huge String", "error": "Server crashed (500) on huge string!"})

    # 5. Security: Missing Token
    res = requests.post(f"{BASE_URL}/agent/chat", json=chat_payload)
    if res.status_code not in [401, 403]:
        errors.append({"case": "Missing Token", "error": f"Expected 401/403, got {res.status_code}"})

    # 6. Concurrent Requests
    print("Testing Concurrency...")
    async with httpx.AsyncClient(timeout=120.0) as client:
        tasks = [
            client.post(f"{BASE_URL}/agent/chat", json={"query": f"Concurrent {i}", "thread_id": "thread_concurrent", "project_id": "default"}, headers=headers)
            for i in range(5)
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        
        for r in responses:
            if isinstance(r, Exception):
                errors.append({"case": "Concurrent Requests", "error": f"Exception: {str(r)}"})
            elif r.status_code != 200:
                errors.append({"case": "Concurrent Requests", "error": f"Failed with {r.status_code}: {r.text}"})
                break

    print("Chat Errors:", errors)
    return errors

if __name__ == "__main__":
    asyncio.run(test_chat_exhaustive())
