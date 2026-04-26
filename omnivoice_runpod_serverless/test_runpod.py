import os
import sys
import time
import requests
import base64
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("RUNPOD_API_KEY")
endpoint_id = os.getenv("RUNPOD_ENDPOINT_ID")

run_url = f"https://api.runpod.ai/v2/{endpoint_id}/run"
headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

# Create a dummy 2MB string
b64 = "A" * (2 * 1024 * 1024)

input_params = {
    "text": "Kiểm tra clone giọng nói",
    "language": "vi",
    "mode": "voice_clone",
    "reference_audio_base64": b64,
    "speed": 1.0,
    "pitch_shift": 1.0,
    "num_step": 10,
    "guidance_scale": 3.0,
    "return_base64": False,
    "save_output": True,
}

print("Submitting job...")
run_resp = requests.post(run_url, json={"input": input_params}, headers=headers, timeout=60)
print(run_resp.status_code, run_resp.text)
job_id = run_resp.json().get("id")

print(f"Job ID: {job_id}")
status_url = f"https://api.runpod.ai/v2/{endpoint_id}/status/{job_id}"
for i in range(25):
    status_resp = requests.get(status_url, headers=headers, timeout=10)
    print(status_resp.status_code, status_resp.text)
    data = status_resp.json()
    if data.get("status") == "COMPLETED":
        print("Done!")
        break
    if data.get("status") == "FAILED":
        print("Failed!")
        break
    time.sleep(2)
