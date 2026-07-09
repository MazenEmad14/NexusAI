import requests
import json

def test_classifier():
    url = "http://localhost:5000/api/v1/agent/chat/stream"
    payload = {
        "query": "حلل ملف البيانات وارسم مخطط بياني للرواتب",
        "project_id": "default",
        "thread_id": "test_thread"
    }
    
    print("Sending request...")
    res = requests.post(url, json=payload)
    print("Status:", res.status_code)

if __name__ == "__main__":
    test_classifier()
