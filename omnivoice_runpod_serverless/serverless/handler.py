from __future__ import annotations

import argparse
import json
import shutil
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

import runpod
from runpod.serverless.utils.rp_cleanup import clean
from runpod.serverless.utils.rp_validator import validate

from app.engine import get_service, get_voice_library_status

INPUT_SCHEMA = {
    "text": {"type": str, "required": True},
    "language": {"type": str, "required": False, "default": "vi"},
    "mode": {"type": str, "required": False, "default": None},
    "ref_text": {"type": str, "required": False, "default": None},
    "reference_audio_path": {"type": str, "required": False, "default": None},
    "reference_audio_url": {"type": str, "required": False, "default": None},
    "reference_audio_base64": {"type": str, "required": False, "default": None},
    "voice_preset": {"type": str, "required": False, "default": None},
    "list_voice_presets": {"type": bool, "required": False, "default": False},
    "emotion": {"type": str, "required": False, "default": "Mặc định"},
    "ad_emphasis": {"type": str, "required": False, "default": "Không bổ trợ"},
    "ad_safe": {"type": bool, "required": False, "default": True},
    "custom_instruct": {"type": str, "required": False, "default": None},
    "speed": {"type": float, "required": False, "default": None},
    "pitch_shift": {"type": float, "required": False, "default": None},
    "num_step": {"type": int, "required": False, "default": None},
    "guidance_scale": {"type": float, "required": False, "default": None},
    "join_silence_ms": {"type": int, "required": False, "default": None},
    "trailing_silence_ms": {"type": int, "required": False, "default": None},
    "max_segment_chars": {"type": int, "required": False, "default": None},
    "output_filename": {"type": str, "required": False, "default": None},
    "return_base64": {"type": bool, "required": False, "default": False},
    "save_output": {"type": bool, "required": False, "default": True},
    "debug": {"type": bool, "required": False, "default": False},
    "preprocess_reference": {"type": bool, "required": False, "default": False},
    "ref_trim_silence": {"type": bool, "required": False, "default": True},
    "ref_trim_top_db": {"type": int, "required": False, "default": 35},
    "ref_apply_vad": {"type": bool, "required": False, "default": True},
    "ref_vad_top_db": {"type": int, "required": False, "default": 32},
    "ref_max_internal_silence_ms": {"type": int, "required": False, "default": 120},
    "ref_apply_denoise": {"type": bool, "required": False, "default": False},
    "ref_denoise_strength": {"type": float, "required": False, "default": 0.18},
    "ref_apply_rms_normalize": {"type": bool, "required": False, "default": True},
    "ref_target_rms_dbfs": {"type": float, "required": False, "default": -22.0},
    "ref_min_seconds": {"type": float, "required": False, "default": 1.5},
    "ref_max_seconds": {"type": float, "required": False, "default": 10.0},
}

def _maybe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    return float(value)

def _maybe_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    return int(value)

def handler(job: Dict[str, Any]) -> Dict[str, Any]:
    work_dir: Path | None = None
    debug_enabled = False
    try:
        raw_input = job["input"]
        validated = validate(raw_input, INPUT_SCHEMA)
        if "errors" in validated:
            return {"ok": False, "error": validated["errors"]}

        params = validated["validated_input"]
        debug_enabled = bool(params.get("debug"))
        work_dir = Path("/tmp") / f"omnivoice_{job.get('id', 'local')}"
        work_dir.mkdir(parents=True, exist_ok=True)

        if params["list_voice_presets"]:
            return {"ok": True, "voice_library": get_voice_library_status()}

        if not params["return_base64"] and not params["save_output"]:
            return {
                "ok": False,
                "error": "At least one of return_base64 or save_output must be true.",
            }

        service = get_service()
        result = service.synthesize(
            text=params["text"],
            language=params["language"],
            mode=params["mode"],
            reference_audio_path=params["reference_audio_path"],
            reference_audio_url=params["reference_audio_url"],
            reference_audio_base64=params["reference_audio_base64"],
            voice_preset=params["voice_preset"],
            ref_text=params["ref_text"],
            emotion=params["emotion"],
            ad_emphasis=params["ad_emphasis"],
            ad_safe=bool(params["ad_safe"]),
            custom_instruct=params["custom_instruct"],
            speed=_maybe_float(raw_input.get("speed")),
            pitch_shift=_maybe_float(raw_input.get("pitch_shift")),
            num_step=_maybe_int(raw_input.get("num_step")),
            guidance_scale=_maybe_float(raw_input.get("guidance_scale")),
            join_silence_ms=_maybe_int(raw_input.get("join_silence_ms")),
            trailing_silence_ms=_maybe_int(raw_input.get("trailing_silence_ms")),
            max_segment_chars=_maybe_int(raw_input.get("max_segment_chars")),
            output_filename=params["output_filename"],
            return_base64=params["return_base64"],
            save_output=params["save_output"],
            debug=debug_enabled,
            preprocess_reference=params["preprocess_reference"],
            ref_trim_silence=params["ref_trim_silence"],
            ref_trim_top_db=int(raw_input.get("ref_trim_top_db", 35)),
            ref_apply_vad=params["ref_apply_vad"],
            ref_vad_top_db=int(raw_input.get("ref_vad_top_db", 32)),
            ref_max_internal_silence_ms=int(raw_input.get("ref_max_internal_silence_ms", 120)),
            ref_apply_denoise=params["ref_apply_denoise"],
            ref_denoise_strength=float(raw_input.get("ref_denoise_strength", 0.18)),
            ref_apply_rms_normalize=params["ref_apply_rms_normalize"],
            ref_target_rms_dbfs=float(raw_input.get("ref_target_rms_dbfs", -22.0)),
            ref_min_seconds=float(raw_input.get("ref_min_seconds", 1.5)),
            ref_max_seconds=float(raw_input.get("ref_max_seconds", 10.0)),
            work_dir=str(work_dir),
        )
        return result

    except Exception as exc:
        response = {
            "ok": False,
            "error": str(exc),
        }
        if debug_enabled:
            response["traceback"] = traceback.format_exc(limit=8)
        return response
    finally:
        try:
            clean()
        except Exception:
            pass
        if work_dir is not None:
            shutil.rmtree(work_dir, ignore_errors=True)

def _load_test_job(args: argparse.Namespace) -> Dict[str, Any]:
    if args.test_input:
        return json.loads(args.test_input)
    if args.test_file:
        return json.loads(Path(args.test_file).read_text(encoding="utf-8"))
    raise ValueError("Either --test_input or --test_file is required.")


def main() -> None:
    parser = argparse.ArgumentParser(description="OmniVoice Runpod handler")
    parser.add_argument("--test_input", type=str, help="Inline JSON payload to invoke the handler locally.")
    parser.add_argument("--test_file", type=str, help="Path to a JSON payload file to invoke the handler locally.")
    args = parser.parse_args()

    if args.test_input or args.test_file:
        response = handler(_load_test_job(args))
        print(json.dumps(response, ensure_ascii=False, indent=2))
        return

    # Pre-warm the service (load model into GPU) during container startup.
    # This reduces the billable execution time of the first request.
    try:
        print("Pre-warming OmniVoice service...")
        get_service()
        print("Pre-warming complete. Worker is ready.")
    except Exception as e:
        print(f"Pre-warming failed: {e}")

    runpod.serverless.start({"handler": handler})


if __name__ == "__main__":
    main()
