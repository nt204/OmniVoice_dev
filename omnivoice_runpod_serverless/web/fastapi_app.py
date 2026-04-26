from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
import base64
import time
import re
import requests
import wave
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.presets import (
    LANGUAGE_LABELS,
    build_prompt_voice_healthcheck,
    canonical_lang,
    get_ad_presets_for_lang,
    get_effective_config,
    get_emotion_presets_for_lang,
    get_voice_preset,
    list_voice_presets,
)
from web.auth import bearer, create_access_token, get_current_user, hash_password, verify_password, JWT_SECRET, JWT_ALGORITHM
import jwt
from web.billing import estimate_cost
from web.db import Base, SessionLocal, engine, get_db, wait_for_database
from web.models import JobCost, RunpodBillingBucket, SynthesisJob, User, VoicePreset

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


DEFAULT_TEXTS: Dict[str, str] = {
    "vi": "Khám phá giải pháp đột phá giúp nâng tầm cuộc sống của bạn ngay hôm nay. Sản phẩm chất lượng vượt trội, thiết kế tinh tế cùng ưu đãi hấp dẫn đang chờ đón bạn sở hữu.",
    "lo": "ພວກເຮົາເຊື່ອວ່າອາຫານທີ່ແຊບແມ່ນຄວາມສຸກຂອງຄອບຄົວ.",
    "km": "សូមជម្រាបសួរ! តើអ្នកកំពុងស្វែងរកផលិតផលដែលល្អបំផុតមែនទេ? មកកាន់យើងឥឡូវនេះ ដើម្បីទទួលបានការបញ្ចុះតម្លៃពិសេស និងគុណភាពដែលអ្នកទុកចិត្តបាន!",
    "th": "เราเชื่อว่าอาหารอร่อยคือสายใยแห่งความสุขของครอบครัว",
    "my": "ယနေ့ခေတ်တွင် နည်းပညာသည် ကျွန်ုပ်တို့၏ဘဝကို ပိုမိုလွယ်ကူစေပါသည်။ အကောင်းဆုံးသော ဝန်ဆောင်မှုများနှင့် ထူးခြားဆန်းသစ်သည့် အတွေ့အကြုံများကို ရယူရန်အတွက် ကျွန်ုပ်တို့နှင့် လက်တွဲလိုက်ပါ။ အောင်မြင်မှုဆီသို့ အတူတူလှမ်းကြပါစို့။",
}


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


def _decimal_to_float(value: Decimal | None) -> float:
    return float(value) if value is not None else 0.0


def _maybe_float_value(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _maybe_decimal_value(value: Any) -> Optional[Decimal]:
    numeric = _maybe_float_value(value)
    if numeric is None:
        return None
    return Decimal(str(numeric))


def _extract_runpod_job_id(job_result: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(job_result, dict):
        return None
    for key in ("id", "jobId", "requestId"):
        value = job_result.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _extract_runpod_execution_seconds(job_result: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(job_result, dict):
        return None
    for key in ("executionTime", "executionTimeInMs", "executionTime_ms"):
        value = _maybe_float_value(job_result.get(key))
        if value is not None:
            return value / 1000.0
    return None


def _extract_actual_cost_fields(result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(result, dict):
        return None
    for container_key in ("billing", "cost", "pricing"):
        container = result.get(container_key)
        if isinstance(container, dict):
            result = container
            break

    total_cost_usd = _maybe_float_value(result.get("total_cost_usd"))
    runpod_cost_usd = _maybe_float_value(result.get("runpod_cost_usd"))
    storage_cost_usd = _maybe_float_value(result.get("storage_cost_usd"))
    bandwidth_cost_usd = _maybe_float_value(result.get("bandwidth_cost_usd"))
    service_fee_usd = _maybe_float_value(result.get("service_fee_usd"))
    compute_seconds = _maybe_float_value(result.get("compute_seconds"))
    audio_duration_sec = _maybe_float_value(result.get("audio_duration_sec"))
    gpu_type = result.get("gpu_type") or result.get("device")
    pricing_version = result.get("pricing_version")
    currency = result.get("currency")
    cost_breakdown_json = result.get("cost_breakdown_json")

    if all(
        value is None
        for value in (
            total_cost_usd,
            runpod_cost_usd,
            storage_cost_usd,
            bandwidth_cost_usd,
            service_fee_usd,
        )
    ):
        return None

    return {
        "pricing_version": pricing_version,
        "audio_duration_sec": audio_duration_sec,
        "compute_seconds": compute_seconds,
        "gpu_type": gpu_type,
        "runpod_cost_usd": runpod_cost_usd,
        "storage_cost_usd": storage_cost_usd,
        "bandwidth_cost_usd": bandwidth_cost_usd,
        "service_fee_usd": service_fee_usd,
        "total_cost_usd": total_cost_usd,
        "currency": currency,
        "cost_breakdown_json": cost_breakdown_json,
    }


def _parse_runpod_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _vn_now() -> datetime:
    return datetime.now(VN_TZ)


def _to_vn_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(VN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _resolve_audio_duration(output_path: Optional[str], fallback_duration: Optional[float]) -> Optional[float]:
    if output_path:
        try:
            with wave.open(str(output_path), "rb") as wav_file:
                sample_rate = wav_file.getframerate()
                frame_count = wav_file.getnframes()
                if sample_rate and frame_count:
                    return round(float(frame_count) / float(sample_rate), 4)
        except Exception:
            pass
    return fallback_duration


def _sync_runpod_billing_buckets(
    *,
    endpoint_id: str,
    api_key: str,
    db: Session,
    start_time: datetime,
    end_time: datetime,
    bucket_size: str = "hour",
) -> int:
    start_utc = start_time.astimezone(timezone.utc)
    end_utc = end_time.astimezone(timezone.utc)
    response = requests.get(
        "https://rest.runpod.io/v1/billing/endpoints",
        headers={"Authorization": f"Bearer {api_key}"},
        params={
            "endpointId": endpoint_id,
            "bucketSize": bucket_size,
            "startTime": start_utc.isoformat().replace("+00:00", "Z"),
            "endTime": end_utc.isoformat().replace("+00:00", "Z"),
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        return 0

    synced = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        bucket_start = _parse_runpod_datetime(item.get("time"))
        if bucket_start is None:
            continue
        resolved_endpoint_id = str(item.get("endpointId") or endpoint_id)
        bucket = (
            db.query(RunpodBillingBucket)
            .filter(
                RunpodBillingBucket.endpoint_id == resolved_endpoint_id,
                RunpodBillingBucket.bucket_size == bucket_size,
                RunpodBillingBucket.bucket_start == bucket_start,
            )
            .one_or_none()
        )
        if bucket is None:
            bucket = RunpodBillingBucket(
                endpoint_id=resolved_endpoint_id,
                bucket_size=bucket_size,
                bucket_start=bucket_start,
            )
            db.add(bucket)
        bucket.amount_usd = Decimal(str(_maybe_float_value(item.get("amount")) or 0.0))
        time_billed = item.get("timeBilledMs")
        bucket.time_billed_ms = int(time_billed) if time_billed is not None else None
        bucket.gpu_type_id = str(item.get("gpuTypeId")) if item.get("gpuTypeId") else None
        bucket.pod_id = str(item.get("podId")) if item.get("podId") else None
        disk_space = item.get("diskSpaceBilledGb")
        bucket.disk_space_billed_gb = int(disk_space) if disk_space is not None else None
        bucket.raw_json = json.dumps(item, ensure_ascii=False)
        bucket.synced_at = datetime.now(timezone.utc)
        synced += 1

    return synced


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip("'").strip('"')
        value = re.sub(r"\$\{([^}]+)\}", lambda match: os.environ.get(match.group(1), match.group(0)), value)
        os.environ[key] = value


def _bootstrap_web_env() -> None:
    here = Path(__file__).resolve().parent
    _load_env_file(here / ".env.runpod-web")
    _load_env_file(here / ".env")


_bootstrap_web_env()


def _output_dir() -> Path:
    output_dir = Path(os.getenv("OUTPUT_DIR", "/tmp/outputs"))
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def get_service() -> Any:
    from app.engine import get_service as get_engine_service

    return get_engine_service()


def get_voice_library_status() -> Dict[str, Any]:
    return build_prompt_voice_healthcheck()


def seed_voice_presets(db: Session) -> None:
    from app.presets import VOICE_DEFINITIONS
    from web.models import VoicePreset

    for lang, definitions in VOICE_DEFINITIONS.items():
        for defn in definitions:
            preset_key = str(defn["preset_key"])
            existing = db.scalar(select(VoicePreset).where(VoicePreset.preset_key == preset_key))
            if not existing:
                tags = ",".join(defn.get("style_tags", []))
                vp = VoicePreset(
                    preset_key=preset_key,
                    label=str(defn["label"]),
                    language=lang,
                    folder=str(defn["folder"]),
                    audio_file=str(defn["audio_file"]),
                    style_tags=tags,
                )
                db.add(vp)
    db.commit()


def _runpod_config() -> tuple[Optional[str], Optional[str]]:
    endpoint_id = os.getenv("RUNPOD_ENDPOINT_ID")
    api_key = os.getenv("RUNPOD_API_KEY")
    return endpoint_id, api_key


def _local_runtime_error_payload(exc: Exception) -> Optional[Dict[str, Any]]:
    message = str(exc)
    local_runtime_markers = (
        "torch is unavailable in this environment",
        "omnivoice is unavailable in this environment",
        "librosa is unavailable in this environment",
        "pedalboard is unavailable in this environment",
        "No module named 'torch'",
        "No module named 'numpy'",
        "No module named 'omnivoice'",
        "No module named 'librosa'",
        "No module named 'pedalboard'",
    )
    if not any(marker in message for marker in local_runtime_markers):
        return None
    return {
        "ok": False,
        "error": (
            "Local OmniVoice runtime is unavailable in this web-only environment. "
            "To test the serverless endpoint from this web UI, set RUNPOD_ENDPOINT_ID "
            "and RUNPOD_API_KEY in the current shell or in omnivoice_runpod_serverless/.env.runpod-web, "
            "then restart the web server."
        ),
        "details": message,
    }


def _resolve_mode(language: str, voice_preset: Optional[str], reference_audio_path: Optional[str]) -> str:
    if reference_audio_path:
        return "clone"
    preset = get_voice_preset(voice_preset) if voice_preset else None
    if preset and preset.get("exists") and canonical_lang(language) == preset.get("language"):
        return "clone"
    return "design"


def _voice_items_for_lang(lang: str) -> List[Dict[str, Any]]:
    lang = canonical_lang(lang)
    items = [item for item in list_voice_presets() if item and item.get("language") == lang]
    return sorted(items, key=lambda item: (not bool(item.get("exists")), str(item.get("label", "")).lower()))


def _voice_choices(lang: str) -> List[Dict[str, Any]]:
    choices: List[Dict[str, Any]] = []
    for item in _voice_items_for_lang(lang):
        label = str(item.get("label") or item.get("preset_key"))
        if not item.get("exists"):
            label = f"{label} (missing audio)"
        elif not item.get("reference_text_exists"):
            label = f"{label} (missing ref text)"
        choices.append({"label": label, "value": str(item["preset_key"])})
    return choices


def _first_voice_key(lang: str) -> Optional[str]:
    choices = _voice_choices(lang)
    return choices[0]["value"] if choices else None


def _resolve_voice_preset_for_lang(lang: str, voice_preset: Optional[str]) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    lang = canonical_lang(lang)
    preset = get_voice_preset(voice_preset) if voice_preset else None
    if preset and preset.get("language") == lang:
        return str(preset["preset_key"]), preset
    fallback_key = _first_voice_key(lang)
    fallback = get_voice_preset(fallback_key) if fallback_key else None
    return (str(fallback["preset_key"]), fallback) if fallback else (None, None)


def _voice_status(lang: str, voice_preset: Optional[str]) -> Dict[str, Any]:
    lang = canonical_lang(lang)
    resolved_key, preset = _resolve_voice_preset_for_lang(lang, voice_preset)
    if not preset:
        return {
            "status": f"{LANGUAGE_LABELS.get(lang, lang)} | 0 voice preset | sẽ chạy bằng design fallback nếu không upload audio ref",
            "reference_text": "",
            "preview_audio_url": None,
            "voice_preset": None,
        }

    preview_audio_url = f"/api/preset-audio/{preset['preset_key']}" if preset.get("exists") else None
    status = f"{LANGUAGE_LABELS.get(lang, lang)} | preset: {preset['label']}"
    if not preset.get("exists"):
        status += " | missing audio"
    elif not preset.get("reference_text_exists"):
        status += " | missing ref text"
    else:
        status += " | clone-ready"
    return {
        "status": status,
        "reference_text": str(preset.get("reference_text") or ""),
        "preview_audio_url": preview_audio_url,
        "voice_preset": resolved_key,
    }


def _state_for_lang(
    lang: str,
    control_mode: str = "preset",
    current_speed: Optional[float] = None,
    current_pitch: Optional[float] = None,
    current_num_step: Optional[float] = None,
    current_guidance_scale: Optional[float] = None,
) -> Dict[str, Any]:
    lang = canonical_lang(lang)
    voice_choices = _voice_choices(lang)
    voice_key = voice_choices[0]["value"] if voice_choices else None
    voice_status = _voice_status(lang, voice_key)
    emotions = list(get_emotion_presets_for_lang(lang).keys())
    ads = list(get_ad_presets_for_lang(lang).keys())
    emotion_key = emotions[0]
    ad_key = ads[0]
    if control_mode == "custom":
        cfg = {
            "speed": current_speed or 1.0,
            "pitch_shift": current_pitch or 1.0,
            "num_step": current_num_step or 52,
            "guidance_scale": current_guidance_scale or 3.9,
        }
    else:
        cfg = get_effective_config(lang, emotion_key, ad_key)
    return {
        "lang": lang,
        "text": DEFAULT_TEXTS.get(lang, ""),
        "voice_choices": voice_choices,
        "voice_status": voice_status,
        "emotions": emotions,
        "ads": ads,
        "emotion": emotion_key,
        "ad_emphasis": ad_key,
        "config": {
            "speed": float(cfg.get("speed", 1.0)),
            "pitch_shift": float(cfg.get("pitch_shift", 1.0)),
            "num_step": int(cfg.get("num_step", 52)),
            "guidance_scale": float(cfg.get("guidance_scale", 3.9)),
        },
    }


def _prosody_state(
    lang: str,
    emotion: str,
    ad_emphasis: str,
    control_mode: str = "preset",
    current_speed: Optional[float] = None,
    current_pitch: Optional[float] = None,
    current_num_step: Optional[float] = None,
    current_guidance_scale: Optional[float] = None,
) -> Dict[str, Any]:
    lang = canonical_lang(lang)
    if control_mode == "custom":
        cfg = {
            "speed": current_speed or 1.0,
            "pitch_shift": current_pitch or 1.0,
            "num_step": current_num_step or 52,
            "guidance_scale": current_guidance_scale or 3.9,
        }
    else:
        cfg = get_effective_config(lang, emotion, ad_emphasis)
    return {
        "config": {
            "speed": float(cfg.get("speed", 1.0)),
            "pitch_shift": float(cfg.get("pitch_shift", 1.0)),
            "num_step": int(cfg.get("num_step", 52)),
            "guidance_scale": float(cfg.get("guidance_scale", 3.9)),
        }
    }


app = FastAPI(title="OmniVoice Production UI", version="1.0.0")
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.on_event("startup")
def startup_initialize_database() -> None:
    wait_for_database()
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        seed_voice_presets(db)


@app.post("/api/auth/register")
async def register(req: RegisterRequest, db: Session = Depends(get_db)):
    # Check if user already exists
    existing = db.scalar(select(User).where(User.email == req.email))
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    user = User(
        email=req.email,
        password_hash=hash_password(req.password),
        role="user",
        is_active=True,
        is_approved=False # Default to pending
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    
    return {"ok": True, "message": "Đăng ký thành công! Vui lòng đợi Admin phê duyệt tài khoản của bạn."}


@app.post("/api/auth/login")
async def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == req.email))
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")
    
    if not user.is_approved:
        raise HTTPException(status_code=403, detail="Tài khoản của bạn đang chờ phê duyệt. Vui lòng liên hệ Admin!")

    user.last_login_at = _vn_now()
    db.commit()
    
    token = create_access_token(user)
    return {"ok": True, "access_token": token, "token_type": "bearer"}


@app.get("/api/auth/me")
async def get_me(current_user: User = Depends(get_current_user)):
    return {
        "ok": True,
        "user": {
            "id": current_user.id,
            "email": current_user.email,
            "role": current_user.role,
            "is_approved": current_user.is_approved,
            "created_at": current_user.created_at
        }
    }

@app.get("/api/admin/users/pending")
async def list_pending_users(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Quyền Admin là bắt buộc")
    users = db.scalars(select(User).where(User.is_approved == False)).all()
    return {
        "ok": True, 
        "users": [{
            "id": u.id,
            "email": u.email,
            "created_at": u.created_at
        } for u in users]
    }

@app.post("/api/admin/users/{user_id}/approve")
async def approve_user(user_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Quyền Admin là bắt buộc")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
    user.is_approved = True
    db.commit()
    return {"ok": True, "message": f"Đã phê duyệt tài khoản {user.email}"}

@app.get("/api/jobs")
def list_jobs(
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    rows = (
        db.execute(
            select(SynthesisJob, JobCost)
            .outerjoin(JobCost, JobCost.job_id == SynthesisJob.id)
            .where(SynthesisJob.user_id == current_user.id)
            .order_by(SynthesisJob.id.desc())
            .limit(max(1, min(limit, 100)))
        )
        .all()
    )
    items: List[Dict[str, Any]] = []
    for job, cost in rows:
        items.append(
            {
                "id": job.id,
                "text": job.text,
                "language": job.language,
                "mode": job.mode,
                "voice_preset": job.voice_preset,
                "status": job.status,
                "output_audio_url": job.output_audio_url,
                "created_at": _to_vn_iso(job.created_at),
                "total_cost_usd": _decimal_to_float(cost.total_cost_usd) if cost else 0.0,
            }
        )
    return {"ok": True, "items": items}


def get_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    db: Session = Depends(get_db),
) -> Optional[User]:
    if credentials is None or not credentials.credentials:
        return None
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = int(payload["sub"])
        return db.scalar(select(User).where(User.id == user_id, User.is_active.is_(True)))
    except Exception:
        return None


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    lang_options = "".join(
        f'<option value="{key}">{key} - {label}</option>' for key, label in LANGUAGE_LABELS.items()
    )
    html = f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>OmniVoice Production UI</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css" rel="stylesheet">
  <style>
    :root {{
      --ov-primary: #0ea5e9;
      --ov-primary-dark: #0369a1;
      --ov-bg: #f0f9ff;
      --ov-card: #ffffff;
      --ov-text: #0f172a;
      --ov-muted: #64748b;
      --ov-line: #e2e8f0;
      --ov-shadow: 0 18px 48px rgba(3, 105, 161, 0.10);
    }}
    body {{
      background-color: var(--ov-bg);
      color: var(--ov-text);
      font-family: 'Inter', system-ui, -apple-system, sans-serif;
      min-height: 100vh;
    }}
    #auth_overlay {{
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: linear-gradient(135deg, #0ea5e9 0%, #0369a1 100%);
      z-index: 9999;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 1.5rem;
    }}
    .auth-card {{
      background: white;
      border-radius: 24px;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
      width: 100%;
      max-width: 420px;
      padding: 2.5rem;
      animation: slideUp 0.5s ease-out;
    }}
    @keyframes slideUp {{
      from {{ opacity: 0; transform: translateY(20px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    #main_content {{
      display: none;
    }}
    .logged-in #main_content {{
      display: block;
    }}
    .logged-in #auth_overlay {{
      display: none;
    }}
    .panel-card {{
      border: 1px solid var(--ov-line);
      border-radius: 20px;
      background: rgba(255,255,255,0.96);
      box-shadow: var(--ov-shadow);
      min-width: 0;
    }}
    .soft-card {{
      border: 1px solid var(--ov-line);
      border-radius: 18px;
      background: #f7fbff;
    }}
    .metric-card {{
      border: 1px solid var(--ov-line);
      border-radius: 18px;
      background: linear-gradient(180deg, #ffffff, #f5fbff);
      padding: 0.85rem 1rem;
      height: 100%;
    }}
    .metric-label {{
      font-size: 0.74rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--ov-muted);
    }}
    .metric-value {{
      font-size: 1rem;
      font-weight: 700;
    }}
    .section-label {{
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--ov-primary-dark);
    }}
    .form-control,
    .form-select {{
      border-color: var(--ov-line);
      border-radius: 12px;
      padding-top: 0.7rem;
      padding-bottom: 0.7rem;
    }}
    .form-control:focus,
    .form-select:focus {{
      border-color: rgba(14, 165, 233, 0.5);
      box-shadow: 0 0 0 0.25rem rgba(14, 165, 233, 0.15);
    }}
    textarea.form-control {{
      min-height: 104px;
      resize: vertical;
    }}
    #ref_text.form-control {{
      min-height: 88px;
    }}
    .range-card {{
      border: 1px solid var(--ov-line);
      border-radius: 14px;
      background: #f8fcff;
      padding: 1rem 1rem 0.9rem;
      height: 100%;
      min-width: 0;
    }}
    .flow-card {{
      border: 1px solid var(--ov-line);
      border-radius: 16px;
      background: #ffffff;
      padding: 1rem;
      height: 100%;
      min-width: 0;
      overflow-x: hidden;
    }}
    .step-dot {{
      width: 2rem;
      height: 2rem;
      border-radius: 999px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: #e0f2fe;
      color: var(--ov-primary-dark);
      font-weight: 700;
      flex: 0 0 auto;
    }}
    .compact-label {{
      font-size: 0.82rem;
      color: var(--ov-muted);
      margin-bottom: 0.45rem;
      font-weight: 600;
    }}
    .preview-grid {{
      display: grid;
      gap: 1rem;
      min-width: 0;
    }}
    .preview-grid > * {{
      min-width: 0;
    }}
    .toolbar-stack {{
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
    }}
    .range-head {{
      display: flex;
      justify-content: space-between;
      gap: 0.75rem;
      margin-bottom: 0.65rem;
      font-size: 0.95rem;
      font-weight: 600;
      color: var(--ov-muted);
    }}
    .range-head strong {{
      color: var(--ov-primary-dark);
      font-size: 1rem;
      white-space: nowrap;
    }}
    .range-meta {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 0.75rem;
      align-items: center;
    }}
    .range-spin {{
      width: 5.5rem;
      text-align: center;
      font-weight: 600;
      border-radius: 10px;
    }}
    .range-card .form-range {{
      min-width: 0;
    }}
    .job-item {{
      padding: 0.75rem 0;
      border-bottom: 1px solid #eee;
      font-size: 0.9rem;
      cursor: pointer;
      min-width: 0;
    }}
    .job-item:last-child {{
      border-bottom: 0;
      padding-bottom: 0;
    }}
    .job-time {{
      font-size: 0.78rem;
      color: var(--ov-muted);
      white-space: nowrap;
    }}
    .job-text {{
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      cursor: pointer;
      transition: all 0.2s ease;
      font-size: 0.9rem;
      color: var(--ov-body);
    }}
    .job-text.expanded {{
      white-space: normal;
      overflow-wrap: anywhere;
      word-break: break-word;
    }}
    .audio-card {{
      border: 1px solid var(--ov-line);
      border-radius: 18px;
      background: linear-gradient(180deg, #ffffff, #f8fcff);
      min-width: 0;
      overflow: hidden;
    }}
    .audio-card audio {{
      width: 100%;
      max-width: 100%;
      min-width: 0;
      display: block;
    }}
    .download-link {{
      text-decoration: none;
    }}
    .download-link[aria-disabled="true"] {{
      pointer-events: none;
      opacity: 0.5;
    }}
    .sticky-side {{
      top: 1.5rem;
    }}
    .compact-hero-title {{
      font-size: clamp(1.7rem, 3vw, 2.3rem);
    }}
    .accordion-button:not(.collapsed) {{
      background: #eef8ff;
      color: var(--ov-primary-dark);
      box-shadow: none;
    }}
    .accordion-button:focus {{
      box-shadow: 0 0 0 0.2rem rgba(14, 165, 233, 0.14);
    }}
    .compact-actions {{
      position: sticky;
      bottom: 12px;
      z-index: 5;
    }}
    @media (max-width: 991.98px) {{
      .sticky-side {{
        position: static !important;
      }}
      .compact-actions {{
        position: static;
      }}
    }}

    /* Docs Modal Styles */
    .docs-modal-content {{
      border-radius: 24px;
      border: none;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.15);
    }}
    .docs-header {{
      background: linear-gradient(135deg, #0ea5e9, #2563eb);
      color: white;
      border-radius: 24px 24px 0 0;
      padding: 1.5rem 2rem;
    }}
    .docs-body {{
      padding: 2rem;
      font-size: 0.95rem;
      line-height: 1.6;
    }}
    .docs-step {{
      display: flex;
      gap: 1rem;
      margin-bottom: 1.5rem;
    }}
    .docs-step-num {{
      flex-shrink: 0;
      width: 28px;
      height: 28px;
      background: #e0f2fe;
      color: #0369a1;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
      font-size: 0.85rem;
    }}
    .docs-step-content h6 {{
      margin-bottom: 0.25rem;
      color: #0f172a;
    }}
    .docs-note {{
      background: #fffbeb;
      border-left: 4px solid #f59e0b;
      padding: 1rem;
      border-radius: 8px;
      margin-top: 1rem;
    }}
  </style>
</head>
<body>
  <div class="container-fluid page-shell py-4 py-lg-5">
    <section class="hero-card p-3 p-lg-4 mb-4">
      <div class="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-2 position-relative">
        <div class="d-flex gap-2">
          <span class="badge rounded-pill text-bg-info-subtle border border-info-subtle text-info-emphasis px-3 py-2">OmniVoice UI</span>
          <span class="badge rounded-pill text-bg-light border px-3 py-2">v1.2 Production</span>
        </div>
        <button class="btn btn-link text-primary text-decoration-none fw-semibold p-0" data-bs-toggle="modal" data-bs-target="#docsModal">
          <i class="bi bi-question-circle me-1"></i>Hướng dẫn nhanh
        </button>
      </div>
      <div class="row g-3 align-items-center position-relative">
        <div class="col-lg-8">
          <h1 class="compact-hero-title fw-semibold mb-1">OmniVoice Production Demo</h1>
          <p class="text-secondary mb-0">Chọn preset, nhập nội dung, generate. Mọi thứ chính nằm trên một luồng ngắn.</p>
        </div>
        <div class="col-lg-4">
          <div class="row g-3">
            <div class="col-6 col-md-3">
              <div class="metric-card">
                <div class="metric-label">Mode</div>
                <div class="metric-value" id="mode_hint">Clone theo preset</div>
              </div>
            </div>
            <div class="col-6 col-md-3">
              <div class="metric-card">
                <div class="metric-label">Source</div>
                <div class="metric-value" id="source_hint">Preset audio</div>
              </div>
            </div>
          </div>
        </div>
      <div class="soft-card p-3 mt-3">
        <div class="row g-3 align-items-end">
          <div class="col-lg-4">
            <label class="form-label fw-semibold">Email</label>
            <input id="auth_email" class="form-control" type="email" placeholder="you@example.com">
          </div>
          <div class="col-lg-3">
            <label class="form-label fw-semibold">Password</label>
            <input id="auth_password" class="form-control" type="password" placeholder="Password">
          </div>
          <div class="col-lg-5">
            <div class="toolbar-stack">
              <button class="btn btn-outline-primary" id="login_btn" type="button">
                <i class="bi bi-box-arrow-in-right me-2"></i>Login
              </button>
              <button class="btn btn-outline-secondary" id="register_btn" type="button">
                <i class="bi bi-person-plus me-2"></i>Register
              </button>
              <button class="btn btn-outline-danger" id="logout_btn" type="button">
                <i class="bi bi-box-arrow-right me-2"></i>Logout
              </button>
            </div>
            <div class="small text-secondary mt-2" id="auth_status">Chưa đăng nhập.</div>
          </div>
        </div>
      </div>
    </section>

    <div class="row g-4 align-items-start">
      <div class="col-xl-8">
        <section class="panel-card p-3 p-lg-4">
          <div class="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-3">
            <div>
              <div class="section-label">Synthesize</div>
              <h2 class="h4 mb-0">Input Flow</h2>
            </div>
            <div class="toolbar-stack">
              <button class="btn btn-outline-primary" id="refresh_btn" type="button">
                <i class="bi bi-arrow-clockwise"></i>
              </button>
              <button class="btn btn-primary" id="generate_btn" type="button">
                <i class="bi bi-stars me-2"></i>Generate
              </button>
            </div>
          </div>

          <div class="row g-3">
            <div class="col-12">
              <div class="flow-card">
                <div class="d-flex align-items-start gap-3">
                  <div class="step-dot">1</div>
                  <div class="flex-grow-1">
                    <div class="row g-3">
                      <div class="col-md-4">
                        <label class="form-label fw-semibold">Ngôn ngữ</label>
                        <select id="lang" class="form-select">{lang_options}</select>
                      </div>
                      <div class="col-md-5">
                        <label class="form-label fw-semibold">Voice preset</label>
                        <select id="voice_preset" class="form-select"></select>
                      </div>
      <div class="col-md-3">
                        <label class="form-label fw-semibold">Control mode</label>
                        <select id="control_mode" class="form-select">
                          <option value="preset">Theo preset</option>
                          <option value="notebook">Notebook test</option>
                          <option value="custom">Tùy chỉnh</option>
                        </select>
                      </div>
                    </div>
                    <div id="preset_status" class="alert alert-info mt-3 mb-0">Đang tải preset...</div>
                  </div>
                </div>
              </div>
            </div>
            <div class="col-12">
              <div class="flow-card">
                <div class="d-flex align-items-start gap-3">
                  <div class="step-dot">2</div>
                  <div class="flex-grow-1">
                    <label class="form-label fw-semibold">Text cần synthesize</label>
                    <textarea id="text" class="form-control"></textarea>
                  </div>
                </div>
              </div>
            </div>
            <div class="col-lg-7">
              <div class="flow-card h-100">
                <div class="d-flex align-items-start gap-3">
                  <div class="step-dot">3</div>
                  <div class="flex-grow-1">
                    <div class="row g-3">
                      <div class="col-sm-6">
                        <label class="form-label fw-semibold">Emotion</label>
                        <select id="emotion" class="form-select"></select>
                      </div>
                      <div class="col-sm-6">
                        <label class="form-label fw-semibold">Ad emphasis</label>
                        <select id="ad_emphasis" class="form-select"></select>
                      </div>
                      <div class="col-sm-8">
                        <label class="form-label fw-semibold">Reference audio</label>
                        <input id="reference_audio" class="form-control" type="file" accept="audio/*">
                      </div>
                      <div class="col-sm-4 d-grid">
                        <button class="btn btn-outline-secondary" type="button" id="reset_preview_btn">
                          <i class="bi bi-x-circle me-2"></i>Reset
                        </button>
                      </div>
                      <div class="col-12">
                        <div class="alert alert-secondary mb-0 py-2" id="input_priority_status">Upload file sẽ được ưu tiên hơn preset audio.</div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            <div class="col-lg-5">
              <div class="flow-card h-100">
                <div class="d-flex align-items-start gap-3">
                  <div class="step-dot">4</div>
                  <div class="flex-grow-1">
                    <div class="accordion" id="advancedAccordion">
                      <div class="accordion-item border-0 bg-transparent">
                        <h2 class="accordion-header">
                          <button class="accordion-button collapsed rounded-3 px-3 py-2" type="button" data-bs-toggle="collapse" data-bs-target="#advancedPanel" aria-expanded="false" aria-controls="advancedPanel">
                            Advanced Settings
                          </button>
                        </h2>
                        <div id="advancedPanel" class="accordion-collapse collapse">
                          <div class="accordion-body px-0 pt-3 pb-0">
                            <div class="vstack gap-3">
                              <div>
                                <label class="form-label fw-semibold">Reference text</label>
                                <textarea id="ref_text" class="form-control"></textarea>
                              </div>
                              <div class="row g-2">
                                <div class="col-12 col-md-6">
                                  <div class="range-card">
                                    <div class="range-head"><span>Speed</span><strong id="speed_value">1.00</strong></div>
                                    <div class="range-meta">
                                      <input id="speed" class="form-range" type="range" min="0.5" max="2.0" step="0.01" value="1.0">
                                      <input id="speed_input" class="form-control range-spin" type="number" min="0.5" max="2.0" step="0.01" value="1.0">
                                    </div>
                                  </div>
                                </div>
                                <div class="col-12 col-md-6">
                                  <div class="range-card">
                                    <div class="range-head"><span>Pitch</span><strong id="pitch_shift_value">1.00</strong></div>
                                    <div class="range-meta">
                                      <input id="pitch_shift" class="form-range" type="range" min="0.5" max="2.0" step="0.01" value="1.0">
                                      <input id="pitch_shift_input" class="form-control range-spin" type="number" min="0.5" max="2.0" step="0.01" value="1.0">
                                    </div>
                                  </div>
                                </div>
                                <div class="col-12 col-md-6">
                                  <div class="range-card">
                                    <div class="range-head"><span>Num step</span><strong id="num_step_value">32</strong></div>
                                    <div class="range-meta">
                                      <input id="num_step" class="form-range" type="range" min="24" max="96" step="1" value="32">
                                      <input id="num_step_input" class="form-control range-spin" type="number" min="24" max="96" step="1" value="32">
                                    </div>
                                  </div>
                                </div>
                                <div class="col-12 col-md-6">
                                  <div class="range-card">
                                    <div class="range-head"><span>Guidance</span><strong id="guidance_scale_value">3.90</strong></div>
                                    <div class="range-meta">
                                      <input id="guidance_scale" class="form-range" type="range" min="2.0" max="5.5" step="0.1" value="3.9">
                                      <input id="guidance_scale_input" class="form-control range-spin" type="number" min="2.0" max="5.5" step="0.1" value="3.9">
                                    </div>
                                  </div>
                                </div>
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div class="d-flex flex-wrap justify-content-between align-items-center gap-2 mt-3">
            <div class="small text-secondary">Advanced chỉ dành cho tinh chỉnh sâu và reference text.</div>
            <button class="btn btn-primary btn-lg px-4 d-lg-none" id="generate_btn_bottom" type="button">
              <i class="bi bi-stars me-2"></i>Generate
            </button>
          </div>
        </section>
      </div>

      <div class="col-xl-4">
        <section class="panel-card p-3 p-lg-4 sticky-side position-sticky">
          <div class="d-flex align-items-center justify-content-between mb-3">
            <div>
              <div class="section-label">Output</div>
              <h2 class="h4 mb-0">Preview & Result</h2>
            </div>
            <a id="download_audio" class="btn btn-outline-primary download-link" href="#" aria-disabled="true" download>
              <i class="bi bi-download"></i>
            </a>
          </div>
          <div id="message" class="alert alert-info py-2">Sẵn sàng.</div>

          <div class="preview-grid">
            <div class="audio-card p-3">
              <div class="d-flex align-items-center justify-content-between mb-2">
                <h3 class="h6 mb-0">Input Preview</h3>
                <span class="badge text-bg-light border">Source</span>
              </div>
              <audio id="input_preview" controls></audio>
              <div class="small text-secondary mt-2" id="input_preview_note">Chưa có file upload, đang dùng preset audio làm nguồn preview.</div>
            </div>
            <div class="audio-card p-3">
              <div class="d-flex align-items-center justify-content-between mb-2">
                <h3 class="h6 mb-0">Preset Preview</h3>
                <span class="badge text-bg-light border">Preset</span>
              </div>
              <audio id="preset_preview" controls></audio>
            </div>
            <div class="audio-card p-3">
              <div class="d-flex align-items-center justify-content-between mb-2">
                <h3 class="h6 mb-0">Generated Audio</h3>
                <span class="badge text-bg-light border">Output</span>
              </div>
              <audio id="audio_out" controls></audio>
            </div>
            <div class="audio-card p-3">
              <div class="d-flex align-items-center justify-content-between mb-2">
                <h3 class="h6 mb-0">Recent Jobs</h3>
                <span class="badge text-bg-light border">DB</span>
              </div>
              <div id="jobs_list" class="small text-secondary">Đăng nhập để xem lịch sử generate.</div>
            </div>
          </div>
        </section>
      </div>
    </div>
  </div>

  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
  <script>
    const ids = [
      "lang", "voice_preset", "emotion", "ad_emphasis", "control_mode", "text", "ref_text",
      "speed", "pitch_shift", "num_step", "guidance_scale",
      "preset_status", "preset_preview", "input_preview", "audio_out", "message"
    ];
    const el = Object.fromEntries(ids.map((id) => [id, document.getElementById(id)]));
    const downloadAudio = document.getElementById("download_audio");
    const referenceAudioInput = document.getElementById("reference_audio");
    const inputPreviewNote = document.getElementById("input_preview_note");
    const modeHint = document.getElementById("mode_hint");
    const sourceHint = document.getElementById("source_hint");
    const vnDateTimeFormatter = new Intl.DateTimeFormat('vi-VN', {{
      timeZone: 'Asia/Ho_Chi_Minh',
      hour: '2-digit',
      minute: '2-digit',
      day: '2-digit',
      month: '2-digit',
    }});
    let recentJobs = [];
    let inputPreviewObjectUrl = null;

    function setMessage(text, tone = "info") {{
      const toneClass = {{
        success: "alert-success",
        warning: "alert-warning",
        neutral: "alert-secondary",
        info: "alert-info",
      }}[tone] || "alert-info";
      el.message.className = `alert ${{toneClass}}`;
      el.message.textContent = text;
    }}

    function setStatusText(text, tone = "info") {{
      const toneClass = {{
        success: "alert-success",
        warning: "alert-warning",
        neutral: "alert-info",
        info: "alert-info",
      }}[tone] || "alert-info";
      el.preset_status.className = `alert mt-3 mb-0 ${{toneClass}}`;
      el.preset_status.textContent = text;
    }}

    function setOutputAudio(url) {{
      if (url) {{
        const resolvedUrl = `${{url}}${{url.includes('?') ? '&' : '?'}}t=${{Date.now()}}`;
        el.audio_out.src = resolvedUrl;
        el.audio_out.load();
        downloadAudio.href = url;
        downloadAudio.setAttribute("aria-disabled", "false");
        return;
      }}
      el.audio_out.removeAttribute("src");
      el.audio_out.load();
      downloadAudio.href = "#";
      downloadAudio.setAttribute("aria-disabled", "true");
    }}

    function setSliderValue(id) {{
      const target = document.getElementById(`${{id}}_value`);
      const raw = el[id].value;
      target.textContent = id === "num_step" ? raw : Number(raw).toFixed(2);
      const spin = document.getElementById(`${{id}}_input`);
      if (spin && spin.value !== raw) {{
        spin.value = raw;
      }}
    }}

    ["speed", "pitch_shift", "num_step", "guidance_scale"].forEach((id) => {{
      el[id].addEventListener("input", () => setSliderValue(id));
      const spin = document.getElementById(`${{id}}_input`);
      if (spin) {{
        spin.addEventListener("input", () => {{
          el[id].value = spin.value;
          setSliderValue(id);
        }});
        spin.addEventListener("change", () => {{
          let value = Number(spin.value);
          const min = Number(spin.min);
          const max = Number(spin.max);
          if (Number.isFinite(min)) value = Math.max(min, value);
          if (Number.isFinite(max)) value = Math.min(max, value);
          el[id].value = String(value);
          spin.value = el[id].value;
          setSliderValue(id);
        }});
      }}
      setSliderValue(id);
    }});

    function formatVnDateTime(value) {{
      if (!value) return '';
      try {{
        return vnDateTimeFormatter.format(new Date(value));
      }} catch (_) {{
        return String(value);
      }}
    }}

    function renderJobs(items, autoSelect = true) {{
      const list = document.getElementById('jobs_list');
      recentJobs = Array.isArray(items) ? items : [];
      if (!recentJobs.length) {{
        list.innerHTML = '<div class="text-secondary">Chưa có job nào trong lịch sử.</div>';
        return;
      }}
      list.innerHTML = recentJobs.map(job => `
        <div class="job-item" onclick="this.querySelector('.job-text').classList.toggle('expanded'); loadJobToUI(${{JSON.stringify(job).replace(/"/g, '&quot;')}}, false)">
          <div style="display:flex; justify-content: space-between; gap: 0.75rem; align-items: center; margin-bottom: 2px;">
            <strong style="font-size: 0.85rem; text-transform: uppercase; color: var(--ov-primary-dark);">${{job.status}}</strong>
            <span class="job-time">${{formatVnDateTime(job.created_at)}}</span>
          </div>
          <div class="job-text">${{job.text}}</div>
        </div>
      `).join('');
      if (autoSelect) {{
        const latestPlayableJob = recentJobs.find(job => job.output_audio_url);
        if (latestPlayableJob) {{
          loadJobToUI(latestPlayableJob, false);
        }}
      }}
    }}

    function prependRecentJob(job) {{
      if (!job || !job.id) return;
      const next = [job, ...recentJobs.filter(item => item.id !== job.id)].slice(0, 10);
      renderJobs(next, false);
    }}

    function fillSelect(node, options, value) {{
      node.innerHTML = "";
      options.forEach((item) => {{
        const opt = document.createElement("option");
        opt.value = item.value ?? item;
        opt.textContent = item.label ?? item;
        node.appendChild(opt);
      }});
      if (value !== undefined && value !== null) {{
        node.value = value;
      }}
    }}

    function clearInputPreview() {{
      if (inputPreviewObjectUrl) {{
        URL.revokeObjectURL(inputPreviewObjectUrl);
        inputPreviewObjectUrl = null;
      }}
      el.input_preview.removeAttribute("src");
      el.input_preview.load();
    }}

    function updateModeHint() {{
      const upload = referenceAudioInput.files[0];
      const presetAvailable = Boolean(el.preset_preview.getAttribute("src"));
      if (upload) {{
        modeHint.textContent = "Clone theo upload";
        sourceHint.textContent = upload.name;
        return;
      }}
      if (presetAvailable) {{
        modeHint.textContent = "Clone theo preset";
        sourceHint.textContent = "Preset audio";
        return;
      }}
      modeHint.textContent = "Design fallback";
      sourceHint.textContent = "No audio";
    }}

    function syncPreviewPriority() {{
      const upload = referenceAudioInput.files[0];
      if (upload) {{
        if (inputPreviewObjectUrl) {{
          URL.revokeObjectURL(inputPreviewObjectUrl);
        }}
        inputPreviewObjectUrl = URL.createObjectURL(upload);
        el.input_preview.src = inputPreviewObjectUrl;
        el.input_preview.load();
        inputPreviewNote.textContent = `Đang preview file upload: ${{upload.name}}`;
        setMessage("Đã nhận audio upload.", "warning");
        updateModeHint();
        return;
      }}
      el.input_preview.src = el.preset_preview.src || "";
      el.input_preview.load();
      inputPreviewNote.textContent = "Chưa có file upload, đang dùng preset audio làm nguồn preview.";
      updateModeHint();
    }}

    async function loadState(lang = el.lang.value) {{
      const params = new URLSearchParams({{
        lang,
        control_mode: el.control_mode.value,
        speed: el.speed.value,
        pitch_shift: el.pitch_shift.value,
        num_step: el.num_step.value,
        guidance_scale: el.guidance_scale.value,
      }});
      const res = await fetch(`/api/state?${{params.toString()}}`);
      const data = await res.json();
      fillSelect(el.voice_preset, data.voice_choices, data.voice_status.voice_preset);
      fillSelect(el.emotion, data.emotions, data.emotion);
      fillSelect(el.ad_emphasis, data.ads, data.ad_emphasis);
      el.text.value = data.text;
      el.ref_text.value = data.voice_status.reference_text || "";
      setStatusText(data.voice_status.status, "neutral");
      el.preset_preview.src = data.voice_status.preview_audio_url || "";
      el.preset_preview.load();
      syncPreviewPriority();
      el.speed.value = data.config.speed;
      el.pitch_shift.value = data.config.pitch_shift;
      el.num_step.value = data.config.num_step;
      el.guidance_scale.value = data.config.guidance_scale;
      ["speed", "pitch_shift", "num_step", "guidance_scale"].forEach(setSliderValue);
      downloadAudio.href = "#";
      downloadAudio.setAttribute("aria-disabled", "true");
      updateModeHint();
    }}

    async function refreshVoiceStatus() {{
      const params = new URLSearchParams({{ lang: el.lang.value, voice_preset: el.voice_preset.value }});
      const res = await fetch(`/api/preset-status?${{params.toString()}}`);
      const data = await res.json();
      if (data.voice_preset && el.voice_preset.value !== data.voice_preset) {{
        el.voice_preset.value = data.voice_preset;
      }}
      el.ref_text.value = data.reference_text || "";
      setStatusText(data.status, "neutral");
      el.preset_preview.src = data.preview_audio_url || "";
      el.preset_preview.load();
      syncPreviewPriority();
    }}

    async function syncProsody() {{
      const params = new URLSearchParams({{
        lang: el.lang.value,
        emotion: el.emotion.value,
        ad_emphasis: el.ad_emphasis.value,
        control_mode: el.control_mode.value,
        speed: el.speed.value,
        pitch_shift: el.pitch_shift.value,
        num_step: el.num_step.value,
        guidance_scale: el.guidance_scale.value,
      }});
      const res = await fetch(`/api/prosody?${{params.toString()}}`);
      const data = await res.json();
      el.speed.value = data.config.speed;
      el.pitch_shift.value = data.config.pitch_shift;
      el.num_step.value = data.config.num_step;
      el.guidance_scale.value = data.config.guidance_scale;
      ["speed", "pitch_shift", "num_step", "guidance_scale"].forEach(setSliderValue);
    }}

    el.lang.addEventListener("change", () => loadState(el.lang.value));
    el.voice_preset.addEventListener("change", refreshVoiceStatus);
    el.control_mode.addEventListener("change", syncProsody);
    el.emotion.addEventListener("change", syncProsody);
    el.ad_emphasis.addEventListener("change", syncProsody);
    referenceAudioInput.addEventListener("change", syncPreviewPriority);

    document.getElementById("refresh_btn").addEventListener("click", async () => {{
      await loadState(el.lang.value);
      setMessage("Preset voice đã được refresh.", "neutral");
    }});

    document.getElementById("reset_preview_btn").addEventListener("click", () => {{
      referenceAudioInput.value = "";
      clearInputPreview();
      syncPreviewPriority();
      setMessage("Đã quay về preset hiện tại.", "neutral");
    }});

    async function runGenerate() {{
      const form = new FormData();
      const token = localStorage.getItem('ov_token');
      form.append("language", el.lang.value);
      form.append("voice_preset", el.voice_preset.value);
      form.append("text", el.text.value);
      form.append("ref_text", el.ref_text.value);
      form.append("emotion", el.emotion.value);
      form.append("ad_emphasis", el.ad_emphasis.value);
      form.append("control_mode", el.control_mode.value);
      form.append("speed", el.speed.value);
      form.append("pitch_shift", el.pitch_shift.value);
      form.append("num_step", el.num_step.value);
      form.append("guidance_scale", el.guidance_scale.value);
      form.append("save_output", "true");
      
      const upload = referenceAudioInput.files[0];
      if (upload) {{
        form.append("reference_audio", upload);
      }}

      setMessage("Đang generate...", "neutral");
      downloadAudio.href = "#";
      downloadAudio.setAttribute("aria-disabled", "true");

      const headers = token ? {{ 'Authorization': `Bearer ${{token}}` }} : {{}};
      const res = await fetch("/api/synthesize", {{ method: "POST", headers, body: form }});
      
      if (res.status === 401) {{
        alert("Vui lòng đăng nhập để sử dụng chức năng này!");
        setMessage("Chưa đăng nhập!", "warning");
        return;
      }}
      if (res.status === 403) {{
        alert("Tài khoản của bạn đang chờ phê duyệt hoặc đã bị khóa. Vui lòng liên hệ Admin!");
        setMessage("Tài khoản chưa được duyệt!", "warning");
        return;
      }}

      const data = await res.json();
      if (!res.ok || !data.ok) {{
        setMessage(data.error || data.detail || "Synthesis failed", "warning");
        return;
      }}

      setMessage(upload ? "Generate thành công với audio upload." : "Generate thành công với preset hiện tại.", "success");
      if (data.audio_url) {{
        setOutputAudio(data.audio_url);
        if (data.job) prependRecentJob(data.job);
        if (typeof refreshJobs === 'function') refreshJobs();
      }} else {{
        setOutputAudio(null);
      }}
    }}

    // Auth & History logic integrated into original UI
    async function login() {{
      const email = document.getElementById('auth_email').value;
      const password = document.getElementById('auth_password').value;
      const res = await fetch('/api/auth/login', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ email, password }})
      }});
      const data = await res.json();
      if (res.ok) {{
        localStorage.setItem('ov_token', data.access_token);
        checkAuth();
        setMessage("Đăng nhập thành công", "success");
      }} else {{
        setMessage(data.detail || "Đăng nhập thất bại", "warning");
      }}
    }}

    async function register() {{
      const email = document.getElementById('auth_email').value;
      const password = document.getElementById('auth_password').value;
      const res = await fetch('/api/auth/register', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ email, password }})
      }});
      const data = await res.json();
      if (res.ok) {{
        localStorage.setItem('ov_token', data.access_token);
        checkAuth();
        setMessage("Đăng ký thành công", "success");
      }} else {{
        let msg = "Đăng ký thất bại";
        if (data.detail) {{
          if (Array.isArray(data.detail)) {{
            msg = data.detail.map(d => d.msg).join(", ");
          }} else {{
            msg = data.detail;
          }}
        }}
        setMessage(msg, "warning");
      }}
    }}

    function logout() {{
      localStorage.removeItem('ov_token');
      checkAuth();
      setMessage("Đã đăng xuất", "neutral");
    }}

    async function checkAuth() {{
      const token = localStorage.getItem('ov_token');
      const statusEl = document.getElementById('auth_status');
      if (!token) {{
        statusEl.textContent = "Chưa đăng nhập.";
        document.getElementById('jobs_list').innerHTML = "Đăng nhập để xem lịch sử generate.";
        return;
      }}
      const res = await fetch('/api/auth/me', {{
        headers: {{ 'Authorization': `Bearer ${{token}}` }}
      }});
      if (res.ok) {{
        const data = await res.json();
        statusEl.textContent = "Đang đăng nhập: " + data.user.email;
        refreshJobs();
      }} else {{
        logout();
      }}
    }}

    async function refreshJobs() {{
      const token = localStorage.getItem('ov_token');
      if (!token) return;
      const res = await fetch('/api/jobs?limit=10', {{
        headers: {{ 'Authorization': `Bearer ${{token}}` }}
      }});
      const data = await res.json();
      if (res.ok) {{
        renderJobs(data.items || [], true);
      }}
    }}

    window.loadJobToUI = (job, announce = true) => {{
      el.lang.value = job.language;
      el.text.value = job.text;
      if (job.output_audio_url) {{
        setOutputAudio(job.output_audio_url);
        if (announce) setMessage("Đã nạp audio từ lịch sử gần đây.", "neutral");
      }} else {{
        setOutputAudio(null);
        if (announce) setMessage("Job lịch sử này không có file audio để phát.", "warning");
      }}
    }};

    document.getElementById('login_btn').addEventListener('click', login);
    document.getElementById('register_btn').addEventListener('click', register);
    document.getElementById('logout_btn').addEventListener('click', logout);
    document.getElementById("generate_btn").addEventListener("click", runGenerate);
    
    const mobileGenerate = document.getElementById("generate_btn_bottom");
    if (mobileGenerate) {{
      mobileGenerate.addEventListener("click", runGenerate);
    }}

    checkAuth();
    loadState("vi");
  </script>

  <!-- Docs Modal -->
  <div class="modal fade" id="docsModal" tabindex="-1" aria-hidden="true">
    <div class="modal-dialog modal-dialog-centered modal-lg">
      <div class="modal-content docs-modal-content">
        <div class="docs-header d-flex align-items-center justify-content-between">
          <h5 class="modal-title fw-bold mb-0"><i class="bi bi-book me-2"></i>Hướng dẫn sử dụng OmniVoice</h5>
          <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Close"></button>
        </div>
        <div class="docs-body">
          <div class="docs-step">
            <div class="docs-step-num">1</div>
            <div class="docs-step-content">
              <h6>Chọn Giọng đọc & Sắc thái</h6>
              <p class="text-secondary mb-0">Chọn ngôn ngữ và Voice preset phù hợp. Bạn có thể kết hợp <b>Cảm xúc (Emotion)</b> và <b>Cường điệu (Ad emphasis)</b>. Hệ thống mới đã hỗ trợ cộng dồn chỉ số để giọng đọc biến chuyển linh hoạt hơn.</p>
            </div>
          </div>
          <div class="docs-step">
            <div class="docs-step-num">2</div>
            <div class="docs-step-content">
              <h6>Nhập văn bản cần đọc</h6>
              <p class="text-secondary mb-0">Nhập nội dung vào ô văn bản. Lưu ý sử dụng dấu chấm, dấu phẩy hợp lý để model ngắt nghỉ đúng nhịp điệu tự nhiên.</p>
            </div>
          </div>
          <div class="docs-step">
            <div class="docs-step-num">3</div>
            <div class="docs-step-content">
              <h6>Tùy chỉnh nâng cao (Advanced)</h6>
              <p class="text-secondary mb-0">Nếu muốn can thiệp sâu, hãy đổi <b>Control mode</b> sang <b>Tùy chỉnh</b>. Bạn có thể kéo Slider để thay đổi Speed, Pitch hoặc Num Step (số bước lấy mẫu - càng cao càng mượt nhưng lâu hơn).</p>
            </div>
          </div>
          <div class="docs-step">
            <div class="docs-step-num">4</div>
            <div class="docs-step-content">
              <h6>Generate & Download</h6>
              <p class="text-secondary mb-0">Nhấn nút <b>Generate</b> và chờ trong giây lát. Kết quả sẽ hiện ở bảng bên phải. Bạn có thể nghe thử preset trước khi tốn token tạo audio mới.</p>
            </div>
          </div>
          <div class="docs-note">
            <div class="fw-bold mb-1"><i class="bi bi-exclamation-triangle me-2"></i>Lưu ý về chất lượng:</div>
            <ul class="mb-0 small text-secondary">
              <li>Khi dùng chức năng voice clone, nên chọn giọng mẫu sạch từ 6-8 giây, đảm bảo văn bản tham chiếu trùng khớp với audio làm mẫu.</li>
              <li>Có thể chỉnh pitch để tăng giảm tone giọng, nếu audio sinh ra bị rè có thể kéo pitch về gần 1, speed để thay đổi tốc độ nói.</li>
              <li>Hệ thống ưu tiên File Upload làm giọng mẫu nếu bạn chọn file ở mục Reference audio.</li>
              <li>Đăng nhập để lưu lịch sử và quản lý job tốt hơn.</li>
            </ul>
          </div>
        </div>
        <div class="modal-footer border-0 pt-0 pb-4 px-4">
          <button type="button" class="btn btn-primary w-100 py-2 rounded-3" data-bs-dismiss="modal">Đã hiểu, bắt đầu dùng!</button>
        </div>
      </div>
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/voice-library")
def voice_library() -> Dict[str, Any]:
    return get_voice_library_status()


@app.get("/api/state")
def state(
    lang: str = "vi",
    control_mode: str = "preset",
    speed: Optional[float] = None,
    pitch_shift: Optional[float] = None,
    num_step: Optional[float] = None,
    guidance_scale: Optional[float] = None,
) -> Dict[str, Any]:
    return _state_for_lang(lang, control_mode, speed, pitch_shift, num_step, guidance_scale)


@app.get("/api/preset-status")
def preset_status(lang: str = "vi", voice_preset: Optional[str] = None) -> Dict[str, Any]:
    return _voice_status(lang, voice_preset or _first_voice_key(lang))


@app.get("/api/prosody")
def prosody(
    lang: str = "vi",
    emotion: str = "Mặc định",
    ad_emphasis: str = "Không bổ trợ",
    control_mode: str = "preset",
    speed: Optional[float] = None,
    pitch_shift: Optional[float] = None,
    num_step: Optional[float] = None,
    guidance_scale: Optional[float] = None,
) -> Dict[str, Any]:
    return _prosody_state(
        lang=lang,
        emotion=emotion,
        ad_emphasis=ad_emphasis,
        control_mode=control_mode,
        current_speed=speed,
        current_pitch=pitch_shift,
        current_num_step=num_step,
        current_guidance_scale=guidance_scale,
    )


@app.get("/api/preset-audio/{preset_key}")
def preset_audio(preset_key: str) -> FileResponse:
    preset = get_voice_preset(preset_key)
    if not preset or not preset.get("exists"):
        raise HTTPException(status_code=404, detail="Preset audio not found")
    audio_path = Path(str(preset["audio_path"]))
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Preset audio file missing")
    return FileResponse(audio_path)


@app.post("/api/synthesize")
async def synthesize(
    language: str = Form("vi"),
    voice_preset: Optional[str] = Form(None),
    text: str = Form(...),
    ref_text: Optional[str] = Form(None),
    emotion: str = Form("Mặc định"),
    ad_emphasis: str = Form("Không bổ trợ"),
    control_mode: str = Form("preset"),
    speed: float = Form(1.0),
    pitch_shift: float = Form(1.0),
    num_step: int = Form(32),
    guidance_scale: float = Form(3.9),
    debug: bool = Form(False),
    save_output: bool = Form(True),
    reference_audio: Optional[UploadFile] = File(None),
    current_user: User = Depends(get_current_user), # Required login
    db: Session = Depends(get_db),
    background_tasks: BackgroundTasks = BackgroundTasks(),
) -> JSONResponse:
    temp_path: Optional[Path] = None
    try:
        reference_audio_path = None
        if reference_audio is not None and reference_audio.filename:
            suffix = Path(reference_audio.filename).suffix or ".wav"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="omnivoice-ui-") as tmp:
                tmp.write(await reference_audio.read())
                temp_path = Path(tmp.name)
            reference_audio_path = str(temp_path)

        request_voice_preset, resolved_preset = _resolve_voice_preset_for_lang(language, voice_preset or None)
        if control_mode == "notebook" and not reference_audio_path and request_voice_preset:
            if resolved_preset and resolved_preset.get("exists"):
                reference_audio_path = str(resolved_preset["audio_path"])
                if not ref_text and resolved_preset.get("reference_text"):
                    ref_text = str(resolved_preset["reference_text"])
                request_voice_preset = None

        mode = _resolve_mode(language=language, voice_preset=request_voice_preset, reference_audio_path=reference_audio_path)
        # Only preprocess if it's a real upload to save time on presets
        should_preprocess_reference = bool(reference_audio_path is not None)
        reference_preprocess_options = {
            "preprocess_reference": should_preprocess_reference,
            "ref_trim_silence": True,
            "ref_trim_top_db": 35,
            "ref_apply_vad": True,
            "ref_vad_top_db": 32,
            "ref_max_internal_silence_ms": 120,
            "ref_apply_denoise": False,
            "ref_denoise_strength": 0.18,
            "ref_apply_rms_normalize": True,
            "ref_target_rms_dbfs": -22.0,
            "ref_min_seconds": 1.5,
            "ref_max_seconds": 10.0,
        }

        # Check if we should use Remote RunPod Serverless instead of local engine
        endpoint_id, api_key = _runpod_config()
        job_result: Optional[Dict[str, Any]] = None

        if endpoint_id:
            # REMOTE MODE: Call RunPod Serverless API
            if not api_key:
                return JSONResponse({"ok": False, "error": "RUNPOD_API_KEY is missing but RUNPOD_ENDPOINT_ID is set."}, status_code=400)

            # Prepare payload for RunPod
            input_params = {
                "text": text,
                "language": canonical_lang(language),
                "mode": mode,
                "voice_preset": request_voice_preset,
                "ref_text": ref_text or None,
                "emotion": emotion,
                "ad_emphasis": ad_emphasis,
                "speed": float(speed),
                "pitch_shift": float(pitch_shift),
                "num_step": int(num_step),
                "guidance_scale": float(guidance_scale),
                "return_base64": True,  # Always get base64 back to save locally
                "save_output": False,
                "debug": bool(debug),
            }
            input_params.update(reference_preprocess_options)

            # If user uploaded a reference audio, convert to base64
            if reference_audio_path:
                file_size = Path(reference_audio_path).stat().st_size
                print(f"Reading reference audio: {file_size / 1024:.1f} KB")
                if file_size > 10 * 1024 * 1024:  # 10MB limit
                    return JSONResponse({"ok": False, "error": "File audio mẫu quá lớn (tối đa 10MB)."}, status_code=400)
                
                def _encode_audio():
                    with open(reference_audio_path, "rb") as f:
                        return base64.b64encode(f.read()).decode("utf-8")
                
                b64_data = await asyncio.to_thread(_encode_audio)
                input_params["reference_audio_base64"] = b64_data
                print(f"Base64 encoding complete. String length: {len(b64_data) / 1024:.1f} KB")

            # Clean up None values to avoid RunPod validator issues
            input_params = {k: v for k, v in input_params.items() if v is not None}

            run_url = f"https://api.runpod.ai/v2/{endpoint_id}/run"
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            
            job_result = None
            try:
                # 1. Submit the job (async)
                payload_str = json.dumps(input_params)
                print(f"Submitting job to RunPod (async). Payload size: {len(payload_str) / 1024:.1f} KB")
                
                def _submit_job():
                    # Increased timeout for slow upload connections
                    return requests.post(run_url, json={"input": input_params}, headers=headers, timeout=(60, 600))
                
                run_resp = await asyncio.to_thread(_submit_job)
                
                if not run_resp.ok:
                    print(f"RunPod Submit Error: {run_resp.status_code} - {run_resp.text}")
                    return JSONResponse({"ok": False, "error": f"RunPod API Error (Run): {run_resp.text}"}, status_code=run_resp.status_code)
                
                job_id = run_resp.json().get("id")
                if not job_id:
                    return JSONResponse({"ok": False, "error": "RunPod API Error: Không nhận được Job ID"}, status_code=500)
                
                # 2. Poll for status
                status_url = f"https://api.runpod.ai/v2/{endpoint_id}/status/{job_id}"
                
                for attempt in range(150): # 5 minutes total
                    try:
                        def _check_status():
                            return requests.get(status_url, headers=headers, timeout=10)
                        
                        status_resp = await asyncio.to_thread(_check_status)
                        if not status_resp.ok:
                            await asyncio.sleep(1.0)
                            continue
                        
                        s_json = status_resp.json()
                        s_status = s_json.get("status")
                        if s_status == "COMPLETED":
                            job_result = s_json.get("output")
                            break
                        elif s_status == "FAILED":
                            return JSONResponse({"ok": False, "error": f"RunPod Job Failed: {s_json.get('error')}"}, status_code=500)
                    except Exception:
                        pass
                    
                    # Ultra-fast polling for the first 3 seconds (0.2s intervals)
                    if attempt < 15: await asyncio.sleep(0.2)
                    elif attempt < 40: await asyncio.sleep(0.5)
                    else: await asyncio.sleep(1.2)
                else:
                    return JSONResponse({"ok": False, "error": "RunPod API Timeout: Hệ thống (RunPod) đang khởi động hoặc quá tải. Vui lòng thử lại sau!"}, status_code=504)


                    
            except requests.exceptions.RequestException as e:
                return JSONResponse({"ok": False, "error": f"Lỗi kết nối tới RunPod (Khởi tạo Job): {str(e)}"}, status_code=502)
            
            result = job_result
            if not result or not result.get("ok"):
                return JSONResponse(result or {"ok": False, "error": "Không nhận được phản hồi hợp lệ từ RunPod."}, status_code=400)

            # Save the received audio locally
            audio_b64 = result.get("audio_base64")
            if audio_b64:
                output_filename = result.get("output_filename") or f"remote_{uuid.uuid4().hex[:8]}.wav"
                local_output_path = _output_dir() / output_filename
                audio_data = base64.b64decode(audio_b64)
                with open(local_output_path, "wb") as f:
                    f.write(audio_data)
                result["output_path"] = str(local_output_path)
        else:
            # LOCAL MODE: Run on local GPU
            try:
                local_return_base64 = not bool(save_output)
                service = get_service()
                result = service.synthesize(
                    text=text,
                    language=canonical_lang(language),
                    mode=mode,
                    reference_audio_path=reference_audio_path,
                    voice_preset=request_voice_preset,
                    ref_text=ref_text or None,
                    emotion=emotion,
                    ad_emphasis=ad_emphasis,
                    speed=float(speed),
                    pitch_shift=float(pitch_shift),
                    num_step=int(num_step),
                    guidance_scale=float(guidance_scale),
                    return_base64=local_return_base64,
                    save_output=bool(save_output),
                    debug=bool(debug),
                    **reference_preprocess_options,
                )
            except Exception as exc:
                runtime_error = _local_runtime_error_payload(exc)
                if runtime_error is not None:
                    return JSONResponse(runtime_error, status_code=503)
                raise
        if not result.get("ok"):
            return JSONResponse(result, status_code=400)

        output_path = result.get("output_path")
        if not output_path and result.get("audio_base64"):
            output_filename = result.get("output_filename") or f"local_{uuid.uuid4().hex[:8]}.wav"
            local_output_path = _output_dir() / output_filename
            local_output_path.write_bytes(base64.b64decode(result["audio_base64"]))
            output_path = str(local_output_path)
            result["output_path"] = output_path
        audio_url = None
        if output_path:
            filename = Path(str(output_path)).name
            audio_url = f"/api/outputs/{filename}"
        payload = dict(result)
        payload["audio_url"] = audio_url

        # Log to DB if user is authenticated
        if current_user:
            try:
                # Resolve effective config for metadata logging
                effective_cfg = get_effective_config(
                    lang=language,
                    emotion_key=emotion,
                    ad_key=ad_emphasis,
                    manual_overrides={
                        "speed": speed,
                        "pitch_shift": pitch_shift,
                        "num_step": num_step,
                        "guidance_scale": guidance_scale,
                    }
                )
                
                logged_cfg = result.get("config") or effective_cfg
                actual_text = str(result.get("text") or text)
                actual_duration_sec = _resolve_audio_duration(output_path, _maybe_float_value(result.get("duration_sec")))
                actual_compute_seconds = _maybe_float_value(result.get("compute_seconds"))
                if actual_compute_seconds is None:
                    actual_compute_seconds = _extract_runpod_execution_seconds(job_result)
                actual_runpod_job_id = _extract_runpod_job_id(job_result)
                created_at_vn = _vn_now()
                reference_meta = result.get("reference") if isinstance(result.get("reference"), dict) else {}
                actual_reference_audio_path = (
                    reference_meta.get("preprocessed_path")
                    or reference_meta.get("source_path")
                    or reference_audio_path
                )
                audit_payload = {
                    "effective_config": logged_cfg,
                    "result_config": result.get("effective_config"),
                    "reference": reference_meta or None,
                    "runpod": {
                        "job_id": actual_runpod_job_id,
                        "status": job_result.get("status") if job_result else None,
                        "execution_seconds": actual_compute_seconds,
                        "raw_execution_time": (
                            job_result.get("executionTime")
                            or job_result.get("executionTimeInMs")
                            or job_result.get("executionTime_ms")
                        ) if job_result else None,
                    } if job_result else None,
                }
                job = SynthesisJob(
                    user_id=current_user.id,
                    text=actual_text,
                    language=canonical_lang(language),
                    mode=mode,
                    voice_preset=request_voice_preset,
                    emotion=emotion,
                    ad_emphasis=ad_emphasis,
                    speed=str(speed),
                    pitch_shift=str(pitch_shift),
                    num_step=num_step,
                    guidance_scale=str(guidance_scale),
                    
                    # Store advanced metadata
                    join_silence_ms=logged_cfg.get("join_silence_ms"),
                    trailing_silence_ms=logged_cfg.get("trailing_silence_ms"),
                    max_segment_chars=logged_cfg.get("max_segment_chars"),
                    gain_db=str(result.get("effective_gain_db", result.get("gain_db", 0.0))),
                    
                    # Full config for audit
                    full_config_json=json.dumps(audit_payload, ensure_ascii=False),
                    
                    status="completed" if result.get("ok") else "failed",
                    error_message=result.get("error") if not result.get("ok") else None,
                    reference_type="upload" if reference_audio_path else ("preset" if request_voice_preset else None),
                    reference_audio_path=actual_reference_audio_path,
                    output_audio_url=audio_url,
                    output_audio_path=str(output_path) if output_path else None,
                    input_chars=len(actual_text),
                    audio_duration_sec=_maybe_decimal_value(actual_duration_sec),
                    compute_seconds=_maybe_decimal_value(actual_compute_seconds),
                    runpod_job_id=actual_runpod_job_id,
                    created_at=created_at_vn,
                    finished_at=created_at_vn if result.get("ok") else None,
                )
                db.add(job)
                db.flush()  # Get job.id

                # Log billing metadata
                cost_record = None
                if result.get("ok"):
                    actual_cost = _extract_actual_cost_fields(result)
                    if actual_cost is not None:
                        breakdown = actual_cost.get("cost_breakdown_json")
                        if isinstance(breakdown, str):
                            try:
                                breakdown_payload = json.loads(breakdown)
                            except json.JSONDecodeError:
                                breakdown_payload = {"raw": breakdown}
                        elif isinstance(breakdown, dict):
                            breakdown_payload = dict(breakdown)
                        else:
                            breakdown_payload = {}
                        breakdown_payload["source"] = "actual"
                        cost_record = JobCost(
                            job_id=job.id,
                            pricing_version=str(actual_cost.get("pricing_version") or os.getenv("PRICING_VERSION", "v1")),
                            input_chars=len(actual_text),
                            audio_duration_sec=_maybe_decimal_value(actual_cost.get("audio_duration_sec", actual_duration_sec)),
                            compute_seconds=_maybe_decimal_value(actual_cost.get("compute_seconds", actual_compute_seconds)),
                            gpu_type=str(actual_cost.get("gpu_type") or result.get("device") or "runpod"),
                            runpod_cost_usd=Decimal(str(actual_cost.get("runpod_cost_usd") or 0.0)),
                            storage_cost_usd=Decimal(str(actual_cost.get("storage_cost_usd") or 0.0)),
                            bandwidth_cost_usd=Decimal(str(actual_cost.get("bandwidth_cost_usd") or 0.0)),
                            service_fee_usd=Decimal(str(actual_cost.get("service_fee_usd") or 0.0)),
                            total_cost_usd=Decimal(str(actual_cost.get("total_cost_usd") or 0.0)),
                            currency=str(actual_cost.get("currency") or "USD"),
                            cost_breakdown_json=json.dumps(breakdown_payload, ensure_ascii=False),
                            created_at=created_at_vn,
                        )
                        db.add(cost_record)
                    elif actual_compute_seconds is not None:
                        est = estimate_cost(
                            text=actual_text,
                            duration_sec=actual_duration_sec,
                            compute_seconds=actual_compute_seconds,
                            gpu_type=result.get("device", "runpod")
                        )
                        breakdown_payload = json.loads(est.cost_breakdown_json)
                        breakdown_payload["source"] = "estimated_from_compute_seconds"
                        cost_record = JobCost(
                            job_id=job.id,
                            pricing_version=est.pricing_version,
                            input_chars=est.input_chars,
                            audio_duration_sec=est.audio_duration_sec,
                            compute_seconds=est.compute_seconds,
                            gpu_type=est.gpu_type,
                            runpod_cost_usd=est.runpod_cost_usd,
                            storage_cost_usd=est.storage_cost_usd,
                            bandwidth_cost_usd=est.bandwidth_cost_usd,
                            service_fee_usd=est.service_fee_usd,
                            total_cost_usd=est.total_cost_usd,
                            currency=est.currency,
                            cost_breakdown_json=json.dumps(breakdown_payload, ensure_ascii=False),
                            created_at=created_at_vn,
                        )
                        db.add(cost_record)

                db.commit()
                payload["job"] = {
                    "id": job.id,
                    "text": actual_text,
                    "language": canonical_lang(language),
                    "mode": mode,
                    "voice_preset": request_voice_preset,
                    "status": job.status,
                    "output_audio_url": audio_url,
                    "created_at": _to_vn_iso(job.created_at),
                    "total_cost_usd": float(cost_record.total_cost_usd) if cost_record else 0.0,
                }

                if endpoint_id and api_key:
                    def _bg_sync_wrapper():
                        with SessionLocal() as bg_db:
                            try:
                                _sync_runpod_billing_buckets(
                                    endpoint_id=endpoint_id,
                                    api_key=api_key,
                                    db=bg_db,
                                    start_time=datetime.now(timezone.utc) - timedelta(hours=2),
                                    end_time=datetime.now(timezone.utc) + timedelta(minutes=5),
                                    bucket_size="hour",
                                )
                                bg_db.commit()
                            except Exception as e:
                                print(f"Background billing sync failed: {e}")
                    
                    background_tasks.add_task(_bg_sync_wrapper)

            except Exception as db_exc:
                db.rollback()
                print(f"Failed to log job to DB: {db_exc}")


        return JSONResponse(payload)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass


@app.get("/api/outputs/{filename}")
def output_audio(filename: str) -> FileResponse:
    if filename != os.path.basename(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    output_path = _output_dir() / filename
    if not output_path.exists():
        raise HTTPException(status_code=404, detail="Output file not found")
    return FileResponse(output_path)


def admin_required(current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required"
        )
    return current_user


@app.get("/api/history")
async def get_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from sqlalchemy.orm import joinedload
    # Join with JobCost to show money for each request
    jobs = db.query(SynthesisJob)\
             .options(joinedload(SynthesisJob.cost))\
             .filter(SynthesisJob.user_id == current_user.id)\
             .order_by(SynthesisJob.created_at.desc()).all()
    
    # Format response to include total_cost_usd clearly
    result = []
    for j in jobs:
        job_dict = {
            "id": j.id,
            "text": j.text[:50] + "...",
            "status": j.status,
            "created_at": _to_vn_iso(j.created_at),
            "duration_sec": float(j.audio_duration_sec or 0),
            "cost_usd": float(j.cost.total_cost_usd) if j.cost else 0.0,
            "currency": j.cost.currency if j.cost else "USD",
            "output_audio_url": j.output_audio_url,
            "language": j.language,
        }
        result.append(job_dict)
    return result


@app.get("/api/billing/stats")
async def get_user_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    from sqlalchemy import func
    total_spent = db.query(func.sum(JobCost.total_cost_usd))\
                    .join(SynthesisJob)\
                    .filter(SynthesisJob.user_id == current_user.id)\
                    .scalar() or 0.0
    
    total_jobs = db.query(SynthesisJob).filter(SynthesisJob.user_id == current_user.id).count()
    
    return {
        "user_email": current_user.email,
        "total_spent_usd": float(total_spent),
        "total_requests": total_jobs,
        "currency": "USD"
    }


@app.get("/api/admin/billing/summary")
async def get_admin_billing_summary(
    admin: User = Depends(admin_required),
    db: Session = Depends(get_db)
):
    from sqlalchemy import func
    # Thống kê theo từng user
    stats = db.query(
        User.email,
        func.count(SynthesisJob.id).label("job_count"),
        func.sum(JobCost.total_cost_usd).label("total_cost")
    ).join(SynthesisJob, User.id == SynthesisJob.user_id)\
     .join(JobCost, SynthesisJob.id == JobCost.job_id)\
     .group_by(User.email)\
     .all()
    
    return [
        {"email": s.email, "total_jobs": s.job_count, "total_spent_usd": float(s.total_cost or 0)}
        for s in stats
    ]


@app.get("/api/admin/runpod-billing/actual")
async def get_actual_runpod_billing(
    admin: User = Depends(admin_required),
    db: Session = Depends(get_db)
):
    endpoint_id, api_key = _runpod_config()
    if endpoint_id and api_key:
        try:
            now_utc = datetime.now(timezone.utc)
            _sync_runpod_billing_buckets(
                endpoint_id=endpoint_id,
                api_key=api_key,
                db=db,
                start_time=now_utc - timedelta(days=7),
                end_time=now_utc + timedelta(minutes=5),
                bucket_size="hour",
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=502, detail=f"RunPod billing sync failed: {exc}")

    rows = (
        db.query(RunpodBillingBucket)
        .order_by(RunpodBillingBucket.bucket_start.desc())
        .limit(168)
        .all()
    )
    total_amount = sum(float(row.amount_usd or 0) for row in rows)
    return {
        "endpoint_id": endpoint_id,
        "bucket_size": "hour",
        "total_amount_usd": total_amount,
        "items": [
            {
                "bucket_start": row.bucket_start,
                "endpoint_id": row.endpoint_id,
                "amount_usd": float(row.amount_usd or 0),
                "time_billed_ms": row.time_billed_ms,
                "gpu_type_id": row.gpu_type_id,
                "pod_id": row.pod_id,
            }
            for row in rows
        ],
    }


def main() -> None:
    import uvicorn

    uvicorn.run("fastapi_app:app", host="0.0.0.0", port=7860, reload=False)


if __name__ == "__main__":
    main()
