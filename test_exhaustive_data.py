import requests
import uuid
import os

BASE_URL = "http://127.0.0.1:5000/api/v1"
errors = []

def test_data_exhaustive():
    print("=== Exhaustive Data & Workspace Testing ===")
    
    unique_email = f"data_tester_{uuid.uuid4().hex[:8]}@example.com"
    password = "StrongPassword123!"
    requests.post(f"{BASE_URL}/auth/local/signup", json={"email": unique_email, "password": password, "name": "Data User"})
    login = requests.post(f"{BASE_URL}/auth/local/signin", json={"email": unique_email, "password": password}).json()
    token = login["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project_id = "default"

    # 3. File Upload - Invalid Extension
    with open("dummy.exe", "wb") as f:
        f.write(b"MZ123")
    with open("dummy.exe", "rb") as f:
        files = {"file": ("dummy.exe", f, "application/octet-stream")}
        res = requests.post(f"{BASE_URL}/data/upload/{project_id}", headers=headers, files=files)
        if res.status_code not in [400, 422]:
            errors.append({"case": "Upload Invalid Ext", "error": f"Expected 400/422, got {res.status_code}"})
    os.remove("dummy.exe")

    # 4. File Upload - Empty File
    with open("empty.pdf", "wb") as f:
        pass
    with open("empty.pdf", "rb") as f:
        files = {"file": ("empty.pdf", f, "application/pdf")}
        res = requests.post(f"{BASE_URL}/data/upload/{project_id}", headers=headers, files=files)
        if res.status_code == 500:
            errors.append({"case": "Upload Empty File", "error": "Server crashed (500)!"})
    os.remove("empty.pdf")

    print("Data Errors:", errors)
    return errors

if __name__ == "__main__":
    test_data_exhaustive()
