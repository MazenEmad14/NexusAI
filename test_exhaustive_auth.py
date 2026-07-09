import requests
import uuid

BASE_URL = "http://127.0.0.1:5000/api/v1"
errors = []

def test_auth_exhaustive():
    print("=== Exhaustive Auth Testing ===")
    
    unique_email = f"test_{uuid.uuid4().hex[:8]}@example.com"
    password = "StrongPassword123!"
    
    # 1. Happy Path
    res = requests.post(f"{BASE_URL}/auth/local/signup", json={"email": unique_email, "password": password, "name": "Valid User"})
    if res.status_code not in [200, 201]:
        errors.append({"case": "Happy Path Signup", "error": f"Failed with {res.status_code}: {res.text}"})

    res = requests.post(f"{BASE_URL}/auth/local/signin", json={"email": unique_email, "password": password})
    if res.status_code != 200:
        errors.append({"case": "Happy Path Signin", "error": f"Failed with {res.status_code}: {res.text}"})
    
    # 2. Edge Case: Long Name
    long_name = "A" * 1000
    res = requests.post(f"{BASE_URL}/auth/local/signup", json={"email": f"long_{uuid.uuid4().hex[:8]}@test.com", "password": password, "name": long_name})
    if res.status_code == 500: # It should gracefully reject or truncate, not crash
        errors.append({"case": "Extremely Long Name", "error": f"Server Crashed (500) on long name!"})

    # 3. Edge Case: Emoji in Name
    res = requests.post(f"{BASE_URL}/auth/local/signup", json={"email": f"emoji_{uuid.uuid4().hex[:8]}@test.com", "password": password, "name": "User 🚀🔥"})
    if res.status_code not in [200, 201]:
        errors.append({"case": "Emoji in Name", "error": f"Failed with {res.status_code}: {res.text}"})

    # 4. Invalid Input: Wrong Password at Login
    res = requests.post(f"{BASE_URL}/auth/local/signin", json={"email": unique_email, "password": "wrongpassword"})
    if res.status_code != 401:
        errors.append({"case": "Wrong Password Login", "error": f"Expected 401, got {res.status_code}"})

    # 5. Missing Fields
    res = requests.post(f"{BASE_URL}/auth/local/signup", json={"email": f"miss_{uuid.uuid4().hex[:8]}@test.com"})
    if res.status_code != 422:
        errors.append({"case": "Missing Password Field", "error": f"Expected 422 Validation Error, got {res.status_code}"})

    # 6. SQL Injection / NoSQL Injection attempt in Email
    res = requests.post(f"{BASE_URL}/auth/local/signin", json={"email": {"$gt": ""}, "password": "password"})
    if res.status_code == 500:
        errors.append({"case": "NoSQL Injection payload", "error": "Server crashed (500) on payload!"})

    print("Auth Errors:", errors)
    return errors

if __name__ == "__main__":
    test_auth_exhaustive()
