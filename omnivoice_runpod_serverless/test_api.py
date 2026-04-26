import requests
import time
import os

base_url = "http://localhost:7860"
audio_path = "/Users/macbook/Downloads/grok-video-6e52d024-b1d3-404d-be31-052243d0b72f-_1_.wav"

# 1. Login to get token
login_data = {
    "email": "test@example.com",
    "password": "password123"
}
print("Logging in...")
login_resp = requests.post(f"{base_url}/api/auth/login", json=login_data)
if not login_resp.ok:
    print(f"Login failed: {login_resp.text}")
    exit(1)

token = login_resp.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# 2. Test Synthesis (Voice Clone) with the specific file
print(f"Sending synthesis request with file: {audio_path}")
files = {
    'reference_audio': (os.path.basename(audio_path), open(audio_path, 'rb'), 'audio/wav')
}
data = {
    'text': 'Chào bạn, đây là giọng nói được clone từ file Grok video của bạn. Hy vọng chất lượng âm thanh tốt.',
    'language': 'vi',
    'control_mode': 'preset',
    'voice_preset': 'vi_nam_ke_chuyen',
    'speed': 1.0,
    'pitch_shift': 1.0,
    'num_step': 32,
    'guidance_scale': 3.9
}

start = time.time()
resp = requests.post(f"{base_url}/api/synthesize", files=files, data=data, headers=headers)
print(f"Status: {resp.status_code}")
print(f"Time: {time.time() - start:.2f}s")

try:
    result = resp.json()
    if result.get("ok"):
        print("Success!")
        print(f"Job ID: {result.get('job', {}).get('id')}")
        print(f"Audio URL: {result.get('audio_url')}")
    else:
        print(f"Error: {result.get('error')}")
except Exception as e:
    print(f"Failed to parse JSON: {e}")
    print(resp.text[:500])
