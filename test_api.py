import requests

url = "http://127.0.0.1:5000/api/v1/agent/chat/stream"
data = {
    "query": "search the web for Egypt and Argentina score match world cup 2026",
    "tenant_id": "test_tenant"
}
headers = {
    "Content-Type": "application/json"
}

try:
    response = requests.post(url, json=data)
    print("Response Status:", response.status_code)
    print("Response Content:")
    print(response.text)
except Exception as e:
    print(f"Error: {e}")
