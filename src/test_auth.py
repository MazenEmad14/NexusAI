import requests
import json

base_url = "http://localhost:5000/api/v1/auth/local"

def test_auth():
    print("Testing signup...")
    res = requests.post(f"{base_url}/signup", json={
        "name": "Test User",
        "email": "test@example.com",
        "password": "password123"
    })
    print(res.status_code, res.text)
    
    print("\nTesting signin with wrong email...")
    res = requests.post(f"{base_url}/signin", json={
        "email": "wrong@example.com",
        "password": "password123"
    })
    print(res.status_code, res.text)
    
    print("\nTesting signin with correct credentials...")
    res = requests.post(f"{base_url}/signin", json={
        "email": "test@example.com",
        "password": "password123"
    })
    print(res.status_code, res.text)

if __name__ == "__main__":
    test_auth()
