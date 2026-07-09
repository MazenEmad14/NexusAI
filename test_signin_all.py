import requests
import json
import uuid
import threading
import time

base_url = "http://localhost:5000/api/v1/auth/local"

def test_signin():
    print("--- 2. Testing Sign In ---")
    
    unique_email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    valid_password = "StrongPassword123!"
    
    # Pre-requisite: Create a user
    requests.post(f"{base_url}/signup", json={
        "name": "Test User",
        "email": unique_email,
        "password": valid_password
    })

    # 2.1 Valid Signin
    print(f"\n[Test 2.1] Valid Signin")
    res1 = requests.post(f"{base_url}/signin", json={
        "email": unique_email,
        "password": valid_password
    })
    print("Status:", res1.status_code)
    try:
        print("Response:", res1.json())
        token = res1.json().get("access_token")
    except:
        print("Response:", res1.text)

    # 2.2 Unregistered Email
    print(f"\n[Test 2.2] Unregistered Email")
    res2 = requests.post(f"{base_url}/signin", json={
        "email": f"notfound_{uuid.uuid4().hex[:8]}@example.com",
        "password": valid_password
    })
    print("Status:", res2.status_code)
    try:
        print("Response:", res2.json())
    except:
        print("Response:", res2.text)

    # 2.3 Wrong Password
    print(f"\n[Test 2.3] Wrong Password")
    res3 = requests.post(f"{base_url}/signin", json={
        "email": unique_email,
        "password": "WrongPassword123!"
    })
    print("Status:", res3.status_code)
    try:
        print("Response:", res3.json())
    except:
        print("Response:", res3.text)

    # 2.4 Multiple Quick Requests (Race Condition Test)
    print(f"\n[Test 2.4] Multiple Quick Requests")
    results = []
    
    def make_request():
        res = requests.post(f"{base_url}/signin", json={
            "email": unique_email,
            "password": valid_password
        })
        results.append(res.status_code)

    threads = []
    for _ in range(10):
        t = threading.Thread(target=make_request)
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    print(f"Status codes from 10 concurrent requests: {results}")

if __name__ == "__main__":
    test_signin()
