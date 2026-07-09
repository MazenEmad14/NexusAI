import requests

def test_origin():
    print("Testing Evil Origin...")
    try:
        r = requests.get("http://127.0.0.1:5000/api/v1/auth/google/login?origin=https://evil.com", allow_redirects=False)
        print(f"Status Code: {r.status_code}")
        print(f"Response: {r.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_origin()
