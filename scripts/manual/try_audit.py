import requests
import uuid

BASE_URL = "http://localhost:5000/api/v1"

def print_header(title):
    print(f"\n{'='*50}\n--- {title} ---\n{'='*50}")

def register_user(email, name, password="Password123!"):
    res = requests.post(f"{BASE_URL}/auth/local/signup", json={
        "name": name,
        "email": email,
        "password": password
    })
    print(f"Sign Up {email}:", res.status_code, res.text)
    return res.status_code == 200

def login_user(email, password="Password123!"):
    res = requests.post(f"{BASE_URL}/auth/local/signin", json={
        "email": email,
        "password": password
    })
    print(f"Sign In {email}:", res.status_code)
    if res.status_code == 200:
        return res.json().get("access_token")
    return None

def chat(token, query, project_id="default", thread_id="default_thread", file_path=None):
    payload = {
        "query": query,
        "project_id": project_id,
        "thread_id": thread_id
    }
    if file_path:
        payload["file_path"] = file_path
    
    headers = {"Authorization": f"Bearer {token}"}
    res = requests.post(f"{BASE_URL}/agent/chat/stream", headers=headers, json=payload, stream=True)
    result = ""
    for line in res.iter_lines():
        if line:
            result += line.decode('utf-8') + "\n"
    print(f"Chat Query: '{query}' -> Status: {res.status_code}")
    print(f"Response snippets: {result[:500]}...")
    return result

def main():
    print_header("1. Auth & Tenant Setup")
    user_a = f"testA_{uuid.uuid4().hex[:6]}@example.com"
    user_b = f"testB_{uuid.uuid4().hex[:6]}@example.com"
    
    register_user(user_a, "Test User A")
    register_user(user_b, "Test User B")
    
    token_a = login_user(user_a)
    token_b = login_user(user_b)
    
    if not token_a or not token_b:
        print("Failed to login users, aborting.")
        return

    print_header("2. Basic Chat (Conversational) - Identity Check")
    chat(token_a, "Who are you?", thread_id="thread1")
    chat(token_a, "How are you doing today?", thread_id="thread1")
    
    print_header("3. Calendar Request Extraction")
    chat(token_a, "Book a meeting tomorrow at 3 PM for 1 hour", thread_id="thread2")
    
    print_header("4. Memory & Thread Testing")
    thread_mem = "thread-memory-test"
    for i in range(1, 4):
        chat(token_a, f"My favorite number is {i}", thread_id=thread_mem)
    chat(token_a, "What is my favorite number?", thread_id=thread_mem)

    print_header("5. Data Analysis Tool")
    with open("dummy.csv", "w") as f:
        f.write("Month,Sales\\nJan,100\\nFeb,200\\nMar,300")
    
    upload_res = requests.post(
        f"{BASE_URL}/data/upload/default",
        headers={"Authorization": f"Bearer {token_a}"},
        data={"description": "dummy sales"},
        files={"file": ("dummy.csv", open("dummy.csv", "rb"), "text/csv")}
    )
    print("CSV Upload:", upload_res.status_code, upload_res.text)
    uploaded_file_path = upload_res.json().get("file_path")
    
    chat(token_a, "Do a data analysis and plot the sales from the dummy file", thread_id="thread3", file_path=uploaded_file_path)
    
    print_header("6. Tenant Isolation Check")
    chat(token_b, "What is my favorite number?", thread_id=thread_mem)

    print_header("7. Gmail Test")
    chat(token_a, "Send an email to my boss at boss@example.com saying I will be late", thread_id="thread4")
    
    print("Testing complete.")

if __name__ == "__main__":
    main()
