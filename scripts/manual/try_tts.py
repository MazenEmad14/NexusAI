import requests

def test_tts():
    try:
        print("Testing TTS API...")
        res = requests.post("http://localhost:5000/api/v1/agent/tts", json={"text": "Hello from the Secretary AI. This is a voice test."})
        if res.status_code == 200:
            print("TTS Success: Received audio stream.")
            # save to a file just to check if it's a valid mp3/audio
            with open("test_output.mp3", "wb") as f:
                f.write(res.content)
            print(f"Saved audio of length {len(res.content)} bytes to test_output.mp3")
        else:
            print(f"TTS Failed: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    test_tts()
