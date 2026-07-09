import httpx
import sys

def check_backend():
    print("Checking backend health...")
    try:
        r = httpx.get("http://127.0.0.1:5000/api/v1/health/services")
        print(f"Status Code: {r.status_code}")
        print(f"Response: {r.json()}")
        if r.status_code == 200:
            return True
        return False
    except Exception as e:
        print(f"Error checking backend: {e}")
        return False

if __name__ == "__main__":
    if check_backend():
        print("\n[SUCCESS] Backend is healthy")
        sys.exit(0)
    else:
        print("\n[FAILED] Backend health check failed")
        sys.exit(1)
