import re

with open("web/fastapi_app.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. _manual_control_defaults
content = content.replace(
    '"max_segment_chars": int(cfg["max_segment_chars"]) if cfg.get("max_segment_chars") else 0,',
    '"max_segment_chars": int(cfg["max_segment_chars"]) if cfg.get("max_segment_chars") else 0,\n        "gain_db": float(cfg.get("gain_db", 0.0)),'
)

# 2. _state_for_lang definition
content = content.replace(
    'current_max_segment_chars: Optional[int] = None,\n) -> Dict[str, Any]:',
    'current_max_segment_chars: Optional[int] = None,\n    current_gain_db: Optional[float] = None,\n) -> Dict[str, Any]:'
)

# 2. _state_for_lang dict assignment
content = content.replace(
    '"max_segment_chars": current_max_segment_chars if current_max_segment_chars is not None else manual_defaults["max_segment_chars"],\n        }',
    '"max_segment_chars": current_max_segment_chars if current_max_segment_chars is not None else manual_defaults["max_segment_chars"],\n            "gain_db": current_gain_db if current_gain_db is not None else manual_defaults["gain_db"],\n        }'
)

# 3. _prosody_state definition
content = content.replace(
    'current_max_segment_chars: Optional[int] = None,\n) -> Dict[str, Any]:',
    'current_max_segment_chars: Optional[int] = None,\n    current_gain_db: Optional[float] = None,\n) -> Dict[str, Any]:'
)

# 3. _prosody_state dict assignment
content = content.replace(
    '"max_segment_chars": current_max_segment_chars if current_max_segment_chars is not None else manual_defaults["max_segment_chars"],\n        }',
    '"max_segment_chars": current_max_segment_chars if current_max_segment_chars is not None else manual_defaults["max_segment_chars"],\n            "gain_db": current_gain_db if current_gain_db is not None else manual_defaults["gain_db"],\n        }'
)

# 4. /api/state
content = content.replace(
    'max_segment_chars: Optional[int] = None,\n) -> Dict[str, Any]:',
    'max_segment_chars: Optional[int] = None,\n    gain_db: Optional[float] = None,\n) -> Dict[str, Any]:'
)
content = content.replace(
    'current_max_segment_chars=max_segment_chars,\n    )',
    'current_max_segment_chars=max_segment_chars,\n        current_gain_db=gain_db,\n    )'
)

# 5. /api/prosody
content = content.replace(
    'max_segment_chars: Optional[int] = None,\n) -> Dict[str, Any]:',
    'max_segment_chars: Optional[int] = None,\n    gain_db: Optional[float] = None,\n) -> Dict[str, Any]:'
)
content = content.replace(
    'current_max_segment_chars=max_segment_chars,\n    )',
    'current_max_segment_chars=max_segment_chars,\n        current_gain_db=gain_db,\n    )'
)

# 6. /api/generate
content = content.replace(
    'max_segment_chars: int = Form(0),\n    current_user',
    'max_segment_chars: int = Form(0),\n    gain_db: float = Form(0.0),\n    current_user'
)

content = content.replace(
    '"max_segment_chars": max_segment_chars,\n                        "status": "queued",',
    '"max_segment_chars": max_segment_chars,\n                        "gain_db": gain_db,\n                        "status": "queued",'
)

content = content.replace(
    'max_segment_chars=max_segment_chars if max_segment_chars > 0 else None,\n                    status="queued",',
    'max_segment_chars=max_segment_chars if max_segment_chars > 0 else None,\n                    gain_db=str(gain_db) if gain_db != 0.0 else None,\n                    status="queued",'
)

content = content.replace(
    '"max_segment_chars": max_segment_chars if max_segment_chars > 0 else None,\n                "output_filename":',
    '"max_segment_chars": max_segment_chars if max_segment_chars > 0 else None,\n                "gain_db": gain_db if gain_db != 0.0 else None,\n                "output_filename":'
)

# 7. HTML
html_block = """                                  <div class="col-sm-6 mb-3">
                                    <div class="range-head"><span>Speed</span><strong id="speed_value">1.00</strong></div>
                                    <div class="range-wrapper">
                                      <input id="speed" class="form-control" type="number" step="0.01" value="1.0">
                                      <input type="range" class="form-range custom-range" min="0.5" max="2.0" step="0.01" id="speed_range">
                                    </div>
                                  </div>
                                  <div class="col-sm-6 mb-3">
                                    <div class="range-head"><span>Volume (dB)</span><strong id="gain_db_value">0.0</strong></div>
                                    <div class="range-wrapper">
                                      <input id="gain_db" class="form-control" type="number" step="0.5" value="0.0">
                                      <input type="range" class="form-range custom-range" min="-10" max="15" step="0.5" id="gain_db_range">
                                    </div>
                                  </div>"""
content = re.sub(
    r'<div class="col-sm-6 mb-3">\s*<div class="range-head"><span>Speed</span><strong id="speed_value">1.00</strong></div>\s*<div class="range-wrapper">\s*<input id="speed" class="form-control" type="number" step="0.01" value="1.0">\s*<input type="range" class="form-range custom-range" min="0.5" max="2.0" step="0.01" id="speed_range">\s*</div>\s*</div>',
    html_block,
    content
)

# 8. JS controlIds
content = content.replace(
    'const controlIds = ["speed", "pitch_shift", "num_step", "guidance_scale", "join_silence_ms", "trailing_silence_ms", "max_segment_chars"];',
    'const controlIds = ["speed", "pitch_shift", "num_step", "guidance_scale", "join_silence_ms", "trailing_silence_ms", "max_segment_chars", "gain_db"];'
)

# 9. JS el setup
content = content.replace(
    'speed: el.speed.value,\n        pitch_shift:',
    'speed: el.speed.value,\n        gain_db: el.gain_db.value,\n        pitch_shift:'
)

content = content.replace(
    'el.speed.value = data.config.speed;\n      el.speed_range.value = data.config.speed;',
    'el.speed.value = data.config.speed;\n      el.speed_range.value = data.config.speed;\n      el.gain_db.value = data.config.gain_db;\n      el.gain_db_range.value = data.config.gain_db;'
)

content = content.replace(
    'form.append("speed", el.speed.value);\n      form.append("pitch_shift",',
    'form.append("speed", el.speed.value);\n      form.append("gain_db", el.gain_db.value);\n      form.append("pitch_shift",'
)

with open("web/fastapi_app.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Patching complete!")
