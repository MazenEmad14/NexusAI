import requests
import json
import uuid

base_url = "http://localhost:5000/api/v1/auth/local"

def test_signup():
    print("--- 1. Testing Sign Up ---")
    unique_email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    
    # 1.1 Valid Signup
    print(f"\n[Test 1.1] Valid Signup with {unique_email}")
    res1 = requests.post(f"{base_url}/signup", json={
        "name": "Test User",
        "email": unique_email,
        "password": "StrongPassword123!"
    })
    print("Status:", res1.status_code)
    try:
        print("Response:", res1.json())
    except:
        print("Response:", res1.text)
        
    # 1.2 Duplicate Email
    print(f"\n[Test 1.2] Duplicate Email Signup")
    res2 = requests.post(f"{base_url}/signup", json={
        "name": "Test User 2",
        "email": unique_email,
        "password": "StrongPassword123!"
    })
    print("Status:", res2.status_code)
    try:
        print("Response:", res2.json())
    except:
        print("Response:", res2.text)

    # 1.3 Invalid Email Format
    print(f"\n[Test 1.3] Invalid Email Format")
    res3 = requests.post(f"{base_url}/signup", json={
        "name": "Test User",
        "email": "invalidemailformat",
        "password": "StrongPassword123!"
    })
    print("Status:", res3.status_code)
    try:
        print("Response:", res3.json())
    except:
        print("Response:", res3.text)

    # 1.4 Weak Password
    print(f"\n[Test 1.4] Weak Password (123)")
    unique_email_2 = f"test_{uuid.uuid4().hex[:8]}@example.com"
    res4 = requests.post(f"{base_url}/signup", json={
        "name": "Weak Pass User",
        "email": unique_email_2,
        "password": "123"
    })
    print("Status:", res4.status_code)
    try:
        print("Response:", res4.json())
    except:
        print("Response:", res4.text)

    # 1.5 Missing Fields
    print(f"\n[Test 1.5] Missing Fields")
    res5 = requests.post(f"{base_url}/signup", json={
        "name": "No Email User",
        "password": "StrongPassword123!"
    })
    print("Status:", res5.status_code)
    try:
        print("Response:", res5.json())
    except:
        print("Response:", res5.text)

if __name__ == "__main__":
    try:
        test_signup()
    except requests.exceptions.ConnectionError:
        print("ERROR: Connection Refused. Server is probably not running on port 5000.")
