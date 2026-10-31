import requests
import imageio_ffmpeg
import os
print("FFMPEG PATH:", imageio_ffmpeg.get_ffmpeg_exe())
print("PATH var:", os.environ.get("PATH", ""))

def test_stt():
    try:
        print("Testing STT API...")
        with open("test_output.mp3", "rb") as f:
            files = {"file": ("test_output.mp3", f, "audio/mpeg")}
            res = requests.post("http://localhost:5000/api/v1/agent/stt", files=files)
        
        if res.status_code == 200:
            print(f"STT Success: {res.json()}")
        else:
            print(f"STT Failed: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    test_stt()
