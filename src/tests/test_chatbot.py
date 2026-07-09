import requests
import json
import time

BASE_URL = "http://localhost:5000/api/v1/agent"

def chat(query, thread_id="test_thread"):
    print(f"\nUser: {query}")
    try:
        start = time.time()
        res = requests.post(f"{BASE_URL}/chat", json={
            "query": query,
            "project_id": "default",
            "thread_id": thread_id
        }, timeout=60)
        
        if res.status_code == 200:
            data = res.json()
            response_text = data.get('response', '')
            status = data.get('status', '')
            action_type = data.get('type', '')
            
            print(f"Chatbot [{action_type}] ({status}):")
            print(f"   {response_text.encode('ascii', 'ignore').decode()}")
            
            if status == "pending_approval":
                print(f"   [!] Action Details: {data.get('action_details')}")
                
            print(f"   (Took {time.time()-start:.2f}s)")
            return data
        else:
            print(f"Error {res.status_code}: {res.text}")
    except Exception as e:
        print(f"Exception: {e}")
    return None

if __name__ == "__main__":
    print("="*50)
    print(" CHATBOT CAPABILITY TEST ")
    print("="*50)
    
    # 1. Simple Greeting
    chat("Hello, who are you and what can you do for me?")
    
    # 2. Add a task
    chat("Can you add a new task for me to finish the project presentation by tomorrow 5 PM?")
    
    # 3. RAG Query
    chat("What are the main features of the DEPI project based on my documents?")
    
    print("\nTest completed.")
