import requests
import json
import os

def test_csv_agent():
    base_url = "http://localhost:5000/api/v1"
    
    file_path = os.path.join(os.path.dirname(__file__), "test_data.csv")
    
    payload = {
        "query": "Analyze this data and create a chart for the salaries of each person.",
        "project_id": "default",
        "thread_id": "test_csv_thread",
        "file_path": file_path
    }
    
    print("Sending request to Data Analysis Agent...")
    res = requests.post(f"{base_url}/agent/chat", json=payload)
    
    if res.status_code == 200:
        data = res.json()
        print(f"Status Code: {res.status_code}")
        print("Response Type:", data.get("type"))
        print("Response Text:", data.get("response", "").encode("utf-8", "ignore").decode("utf-8"))
        print("Chart Data:", json.dumps(data.get("chart_data", {}), indent=2))
    else:
        print(f"Error {res.status_code}: {res.text}")

if __name__ == "__main__":
    test_csv_agent()
