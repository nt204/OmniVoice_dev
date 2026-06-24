import re

with open("web/fastapi_app.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Signature of synthesize
content = content.replace(
    'max_segment_chars: int = Form(0),\n    debug: bool',
    'max_segment_chars: int = Form(0),\n    gain_db: float = Form(0.0),\n    debug: bool'
)

# 2. SynthesisJob payload creation
content = content.replace(
    '"max_segment_chars": max_segment_chars,\n                        "status": "queued",',
    '"max_segment_chars": max_segment_chars,\n                        "gain_db": gain_db,\n                        "status": "queued",'
)

# 3. runpod_payload creation
content = content.replace(
    '"max_segment_chars": max_segment_chars if max_segment_chars > 0 else None,\n                "output_filename":',
    '"max_segment_chars": max_segment_chars if max_segment_chars > 0 else None,\n                "gain_db": gain_db if abs(gain_db) > 1e-6 else None,\n                "output_filename":'
)

# 4. Job assignment
content = content.replace(
    'max_segment_chars=max_segment_chars if max_segment_chars > 0 else None,\n                    status="queued",',
    'max_segment_chars=max_segment_chars if max_segment_chars > 0 else None,\n                    gain_db=str(gain_db) if abs(gain_db) > 1e-6 else None,\n                    status="queued",'
)


with open("web/fastapi_app.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Patching complete!")
