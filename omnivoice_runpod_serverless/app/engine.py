from __future__ import annotations

import gc
import glob
import hashlib
import inspect
import json
import math
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import soundfile as sf

from .audio_processing import (
    DEFAULT_TARGET_SR,
    encode_wav_base64,
    ensure_dir,
    preprocess_reference_audio,
    resolve_reference_audio_source,
    write_output_wav,
)
from .presets import (
    LANGUAGE_LABELS,
    build_prompt_voice_healthcheck,
    canonical_lang,
    clamp_prosody,
    get_effective_config,
    safe_gain_for_style,
    get_voice_preset,
)
from .text_normalization import (
    add_config_text_omni,
    build_ref_text,
    preprocess_text_for_tts,
    reference_quality_note,
    segment_text_with_pauses,
)

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

_TORCH_MODULE: Any = None
_TORCH_IMPORT_ERROR: Optional[Exception] = None
_LIBROSA_MODULE: Any = None
_LIBROSA_IMPORT_ERROR: Optional[Exception] = None
_PEDALBOARD_MODULE: Any = None
_PEDALBOARD_IMPORT_ERROR: Optional[Exception] = None
_OMNIVOICE_CLASSES: Optional[Tuple[Any, Any]] = None
_OMNIVOICE_IMPORT_ERROR: Optional[Exception] = None


def _get_torch_module() -> Any:
    global _TORCH_MODULE, _TORCH_IMPORT_ERROR
    if _TORCH_MODULE is not None:
        return _TORCH_MODULE
    if _TORCH_IMPORT_ERROR is not None:
        raise RuntimeError(f"torch is unavailable in this environment: {_TORCH_IMPORT_ERROR}") from _TORCH_IMPORT_ERROR
    try:
        import torch as torch_module
    except Exception as exc:
        _TORCH_IMPORT_ERROR = exc
        raise RuntimeError(f"torch is unavailable in this environment: {exc}") from exc
    _TORCH_MODULE = torch_module
    return _TORCH_MODULE


def _get_librosa_module() -> Any:
    global _LIBROSA_MODULE, _LIBROSA_IMPORT_ERROR
    if _LIBROSA_MODULE is not None:
        return _LIBROSA_MODULE
    if _LIBROSA_IMPORT_ERROR is not None:
        raise RuntimeError(f"librosa is unavailable in this environment: {_LIBROSA_IMPORT_ERROR}") from _LIBROSA_IMPORT_ERROR
    try:
        import librosa as librosa_module
    except Exception as exc:
        _LIBROSA_IMPORT_ERROR = exc
        raise RuntimeError(f"librosa is unavailable in this environment: {exc}") from exc
    _LIBROSA_MODULE = librosa_module
    return _LIBROSA_MODULE


def _get_pedalboard_pitch_shift() -> Any:
    global _PEDALBOARD_MODULE, _PEDALBOARD_IMPORT_ERROR
    if _PEDALBOARD_MODULE is not None:
        return _PEDALBOARD_MODULE
    if _PEDALBOARD_IMPORT_ERROR is not None:
        raise RuntimeError(f"pedalboard is unavailable in this environment: {_PEDALBOARD_IMPORT_ERROR}") from _PEDALBOARD_IMPORT_ERROR
    try:
        from pedalboard import Pedalboard, PitchShift
    except Exception as exc:
        _PEDALBOARD_IMPORT_ERROR = exc
        raise RuntimeError(f"pedalboard is unavailable in this environment: {exc}") from exc
    _PEDALBOARD_MODULE = (Pedalboard, PitchShift)
    return _PEDALBOARD_MODULE


def _get_omnivoice_classes() -> Tuple[Any, Any]:
    global _OMNIVOICE_CLASSES, _OMNIVOICE_IMPORT_ERROR
    if _OMNIVOICE_CLASSES is not None:
        return _OMNIVOICE_CLASSES
    if _OMNIVOICE_IMPORT_ERROR is not None:
        raise RuntimeError(f"omnivoice is unavailable in this environment: {_OMNIVOICE_IMPORT_ERROR}") from _OMNIVOICE_IMPORT_ERROR
    try:
        from omnivoice import OmniVoice as omnivoice_cls
    except Exception:
        try:
            from omnivoice.models.omnivoice import OmniVoice as omnivoice_cls
        except Exception as exc:
            _OMNIVOICE_IMPORT_ERROR = exc
            raise RuntimeError(f"omnivoice is unavailable in this environment: {exc}") from exc
    try:
        from omnivoice.models.omnivoice import VoiceClonePrompt as voice_clone_prompt_cls
    except Exception:
        voice_clone_prompt_cls = None
    _OMNIVOICE_CLASSES = (omnivoice_cls, voice_clone_prompt_cls)
    return _OMNIVOICE_CLASSES


def _apply_output_peak_guard(audio_np: np.ndarray, target_peak: float = 0.92) -> tuple[np.ndarray, float]:
    if audio_np.size == 0:
        return audio_np.astype(np.float32), 0.0
    peak = float(np.max(np.abs(audio_np)))
    if peak <= 0.0 or peak <= float(target_peak):
        return audio_np.astype(np.float32), 0.0
    attenuation = float(target_peak) / peak
    attenuation_db = 20.0 * math.log10(max(attenuation, 1e-8))
    return (audio_np * attenuation).astype(np.float32), attenuation_db

def _normalize_device_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized or normalized == "auto":
        return None
    if normalized not in {"cpu", "cuda", "mps"}:
        raise ValueError("OMNIVOICE_DEVICE must be one of: auto, cpu, cuda, mps")
    return normalized

def _is_device_available(device: str) -> bool:
    if device == "cpu":
        return True
    torch = _get_torch_module()
    if device == "cpu":
        return True
    if device == "cuda":
        return bool(torch.cuda.is_available())
    if device == "mps":
        return bool(hasattr(torch.backends, "mps") and torch.backends.mps.is_available())
    return False

def get_best_device() -> str:
    forced = _normalize_device_name(os.getenv("OMNIVOICE_DEVICE"))
    if forced:
        if not _is_device_available(forced):
            raise RuntimeError(f"OMNIVOICE_DEVICE={forced} was requested but is not available in this environment")
        return forced
    try:
        torch = _get_torch_module()
    except RuntimeError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"

def _candidate_hf_cache_roots() -> list[Path]:
    candidates: list[Path] = []
    for env_key in ("MODEL_LOCAL_PATH", "HF_HOME", "TRANSFORMERS_CACHE"):
        value = os.getenv(env_key)
        if value:
            candidates.append(Path(value))
    candidates.extend(
        [
            Path("/runpod-volume/huggingface-cache"),
            Path("/workspace/huggingface-cache"),
            Path.home() / ".cache" / "huggingface",
        ]
    )

    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique

def resolve_hf_cached_model_path(model_id: str) -> Optional[str]:
    if "/" not in model_id:
        return None
    org, name = model_id.split("/", 1)
    for cache_root in _candidate_hf_cache_roots():
        root = cache_root / "hub" / f"models--{org}--{name}"
        refs_main = root / "refs" / "main"
        if refs_main.exists():
            try:
                snapshot_hash = refs_main.read_text(encoding="utf-8").strip()
                snapshot_path = root / "snapshots" / snapshot_hash
                if snapshot_path.exists():
                    return str(snapshot_path)
            except Exception:
                pass
        snapshots = sorted(glob.glob(str(root / "snapshots" / "*")))
        if snapshots:
            return snapshots[-1]
    return None

def resolve_model_source(model_id: str) -> str:
    explicit = os.getenv("MODEL_LOCAL_PATH")
    if explicit and Path(explicit).exists():
        return explicit
    cached = resolve_hf_cached_model_path(model_id)
    if cached:
        return cached
    return model_id

def infer_mode(reference_audio_path: Optional[str], custom_instruct: Optional[str], explicit_mode: Optional[str]) -> str:
    if explicit_mode in {"clone", "design", "auto"}:
        return explicit_mode
    if reference_audio_path:
        return "clone"
    if custom_instruct and custom_instruct.strip():
        return "design"
    return "auto"

class OmniVoiceService:
    def __init__(
        self,
        model_id: str = "k2-fsa/OmniVoice",
        output_dir: Optional[str] = None,
        prompt_cache_dir: Optional[str] = None,
    ) -> None:
        self.model_id = model_id
        self.model_source = resolve_model_source(model_id)
        self.device = get_best_device()
        self.dtype: Any = None
        self.output_dir = Path(output_dir or os.getenv("OUTPUT_DIR", "/runpod-volume/outputs" if Path("/runpod-volume").exists() else "/tmp/outputs"))
        self.prompt_cache_dir = Path(prompt_cache_dir or os.getenv("PROMPT_CACHE_DIR", "/runpod-volume/prompt-cache" if Path("/runpod-volume").exists() else "/tmp/prompt-cache"))
        ensure_dir(self.output_dir)
        ensure_dir(self.prompt_cache_dir)
        self.model: Optional[Any] = None
        self._prompt_cache: Dict[str, Any] = {}
        self._cache_file = self.prompt_cache_dir / "voice_clone_prompt_cache.json"
        self._load_cache_from_disk()

    def load_model(self) -> Any:
        if self.model is None:
            torch = _get_torch_module()
            omnivoice_cls, _ = _get_omnivoice_classes()
            self.dtype = torch.float16 if self.device != "cpu" else torch.float32
            self.model = omnivoice_cls.from_pretrained(
                self.model_source,
                dtype=self.dtype,
            )
            if hasattr(self.model, "to"):
                self.model = self.model.to(self.device)
        return self.model

    @property
    def sampling_rate(self) -> int:
        model = self.load_model()
        return int(getattr(model, "sampling_rate", DEFAULT_TARGET_SR))

    def _load_cache_from_disk(self) -> None:
        if self._cache_file.exists():
            try:
                self._prompt_cache = json.loads(self._cache_file.read_text(encoding="utf-8"))
            except Exception:
                self._prompt_cache = {}

    def _save_cache_to_disk(self) -> None:
        tmp_file = self._cache_file.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(self._prompt_cache, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_file.replace(self._cache_file)

    @staticmethod
    def _file_fingerprint(file_path: str) -> str:
        p = Path(file_path)
        stat = p.stat()
        hasher = hashlib.md5()
        with p.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        # We exclude mtime to allow cache hits for re-processed files with same content
        return f"{stat.st_size}:{hasher.hexdigest()}"

    def _cache_key(self, ref_audio: str, ref_text: Optional[str]) -> str:
        raw = f"{self._file_fingerprint(ref_audio)}::{ref_text or ''}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    def _prompt_to_serializable(self, prompt: Any) -> Dict[str, Any]:
        ref_audio_tokens = getattr(prompt, "ref_audio_tokens", None)
        ref_text = getattr(prompt, "ref_text", None)
        ref_rms = getattr(prompt, "ref_rms", None)

        if ref_audio_tokens is None:
            raise ValueError("voice clone prompt has no ref_audio_tokens")

        if hasattr(ref_audio_tokens, "cpu"):
            ref_audio_tokens = ref_audio_tokens.cpu().tolist()

        if isinstance(ref_rms, (np.floating, np.integer)):
            ref_rms = float(ref_rms)
        elif hasattr(ref_rms, "item"):
            ref_rms = float(ref_rms.item())

        return {
            "ref_audio_tokens": ref_audio_tokens,
            "ref_text": ref_text,
            "ref_rms": ref_rms,
        }

    def _get_cached_prompt(self, key: str) -> Optional[Any]:
        item = self._prompt_cache.get(key)
        if item is None:
            return None
        torch = _get_torch_module()
        _, voice_clone_prompt_cls = _get_omnivoice_classes()
        if voice_clone_prompt_cls is None:
            return None
        try:
            return voice_clone_prompt_cls(
                ref_audio_tokens=torch.tensor(item["ref_audio_tokens"], dtype=torch.long),
                ref_text=item.get("ref_text"),
                ref_rms=item.get("ref_rms"),
            )
        except Exception:
            return None

    def create_voice_clone_prompt(
        self,
        ref_audio: str,
        ref_text: Optional[str],
        preprocess_prompt: bool = True,
        language: Optional[str] = None,
    ) -> Any:
        model = self.load_model()
        key = self._cache_key(ref_audio, ref_text)
        cached = self._get_cached_prompt(key)
        if cached is not None:
            return cached

        create_prompt_sig = inspect.signature(model.create_voice_clone_prompt)
        prompt_kwargs = {
            "ref_audio": ref_audio,
            "ref_text": ref_text,
            "preprocess_prompt": preprocess_prompt,
        }
        if "language" in create_prompt_sig.parameters and language is not None:
            prompt_kwargs["language"] = language

        prompt = model.create_voice_clone_prompt(**prompt_kwargs)
        try:
            self._prompt_cache[key] = self._prompt_to_serializable(prompt)
            self._save_cache_to_disk()
        except Exception:
            pass
        return prompt

    def _apply_pitch_shift(self, audio_np: np.ndarray, pitch_shift: float) -> np.ndarray:
        if pitch_shift is None or abs(float(pitch_shift) - 1.0) < 1e-6:
            return audio_np.astype(np.float32)
        ratio = max(0.5, min(2.0, float(pitch_shift)))
        semitones = 12.0 * math.log2(ratio)
        try:
            pedalboard_cls, pitch_shift_cls = _get_pedalboard_pitch_shift()
            board = pedalboard_cls([pitch_shift_cls(semitones=semitones)])
            shifted = board(audio_np.reshape(1, -1).astype(np.float32), self.sampling_rate).flatten()
            return shifted.astype(np.float32)
        except RuntimeError:
            librosa = _get_librosa_module()
            shifted = librosa.effects.pitch_shift(audio_np.astype(np.float32), sr=self.sampling_rate, n_steps=semitones)
            return shifted.astype(np.float32)

    def get_prompt_voice_healthcheck(self) -> Dict[str, Any]:
        return build_prompt_voice_healthcheck()

    def _resolve_preset_reference(
        self,
        *,
        voice_preset: Optional[str],
        language: str,
        reference_audio_path: Optional[str],
        reference_audio_url: Optional[str],
        reference_audio_base64: Optional[str],
        ref_text: Optional[str],
        emotion: str,
        ad_emphasis: str,
        speed: Optional[float],
        pitch_shift: Optional[float],
        num_step: Optional[int],
        guidance_scale: Optional[float],
    ) -> Dict[str, Any]:
        preset = get_voice_preset(voice_preset) if voice_preset else None
        if voice_preset and not preset:
            raise ValueError(f"Unknown voice_preset: {voice_preset}")

        if preset and language and canonical_lang(language) != preset["language"]:
            raise ValueError(
                f"voice_preset '{preset['preset_key']}' belongs to language={preset['language']}, not {canonical_lang(language)}"
            )

        resolved_reference_audio_path = reference_audio_path
        resolved_ref_text = ref_text
        resolved_emotion = emotion
        resolved_ad_emphasis = ad_emphasis
        preset_meta: Optional[Dict[str, Any]] = None
        has_manual_reference = any(
            bool(item)
            for item in (reference_audio_path, reference_audio_url, reference_audio_base64)
        )
        manual_overrides = {
            "speed": speed,
            "pitch_shift": pitch_shift,
            "num_step": num_step,
            "guidance_scale": guidance_scale,
        }

        if preset:
            preset_path = Path(preset["audio_path"])
            using_preset_audio = not has_manual_reference
            if using_preset_audio and resolved_reference_audio_path is None:
                if not preset_path.exists():
                    raise FileNotFoundError(
                        f"voice_preset '{preset['preset_key']}' expects audio at {preset_path}, but file was not found"
                    )
                resolved_reference_audio_path = str(preset_path)

            if using_preset_audio and not resolved_ref_text and preset.get("reference_text"):
                resolved_ref_text = str(preset["reference_text"])

            preset_meta = {
                "preset_key": preset["preset_key"],
                "label": preset["label"],
                "audio_path": preset["audio_path"],
                "reference_text_path": preset["reference_text_path"],
                "reference_text_exists": preset["reference_text_exists"],
                "default_config": dict(preset.get("default_config") or {}),
                "style_tags": preset.get("style_tags", []),
                "source": "preset_audio" if using_preset_audio else "manual_reference_override",
                "selection": {
                    "emotion": resolved_emotion,
                    "ad_emphasis": resolved_ad_emphasis,
                    "manual_overrides": manual_overrides,
                },
            }

        return {
            "reference_audio_path": resolved_reference_audio_path,
            "ref_text": resolved_ref_text,
            "emotion": resolved_emotion,
            "ad_emphasis": resolved_ad_emphasis,
            "manual_overrides": manual_overrides,
            "preset_meta": preset_meta,
        }

    def synthesize(
        self,
        *,
        text: str,
        language: str,
        mode: Optional[str] = None,
        reference_audio_path: Optional[str] = None,
        reference_audio_url: Optional[str] = None,
        reference_audio_base64: Optional[str] = None,
        voice_preset: Optional[str] = None,
        ref_text: Optional[str] = None,
        emotion: str = "Mặc định",
        ad_emphasis: str = "Không bổ trợ",
        ad_safe: bool = True,
        custom_instruct: Optional[str] = None,
        speed: Optional[float] = None,
        pitch_shift: Optional[float] = None,
        num_step: Optional[int] = None,
        guidance_scale: Optional[float] = None,
        output_filename: Optional[str] = None,
        return_base64: bool = False,
        save_output: bool = True,
        debug: bool = False,
        preprocess_reference: bool = False,
        ref_trim_silence: bool = True,
        ref_trim_top_db: int = 35,
        ref_apply_vad: bool = True,
        ref_vad_top_db: int = 32,
        ref_max_internal_silence_ms: int = 120,
        ref_apply_denoise: bool = False,
        ref_denoise_strength: float = 0.18,
        ref_apply_rms_normalize: bool = True,
        ref_target_rms_dbfs: float = -22.0,
        ref_min_seconds: float = 1.5,
        ref_max_seconds: float = 10.0,
        work_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        language = canonical_lang(language)
        preset_resolution = self._resolve_preset_reference(
            voice_preset=voice_preset,
            language=language,
            reference_audio_path=reference_audio_path,
            reference_audio_url=reference_audio_url,
            reference_audio_base64=reference_audio_base64,
            ref_text=ref_text,
            emotion=emotion,
            ad_emphasis=ad_emphasis,
            speed=speed,
            pitch_shift=pitch_shift,
            num_step=num_step,
            guidance_scale=guidance_scale,
        )
        reference_audio_path = preset_resolution["reference_audio_path"]
        ref_text = preset_resolution["ref_text"]
        emotion = preset_resolution["emotion"]
        ad_emphasis = preset_resolution["ad_emphasis"]
        manual_overrides = preset_resolution["manual_overrides"]
        preset_meta = preset_resolution["preset_meta"]
        preset_default_config = dict(preset_meta.get("default_config") or {}) if preset_meta else {}

        cfg = get_effective_config(
            language,
            emotion,
            ad_emphasis,
            manual_overrides,
            base_overrides=preset_default_config,
        )
        cfg = clamp_prosody(language, cfg)
        effective_gain_db = safe_gain_for_style(language, emotion, ad_emphasis, enabled=ad_safe)
        work_dir = work_dir or tempfile.mkdtemp(prefix="omnivoice-job-")
        ensure_dir(work_dir)
        model = self.load_model()
        started_at = time.perf_counter()

        normalized_text = preprocess_text_for_tts(text, language, is_reference=False)
        if not normalized_text:
            raise ValueError("text is empty after normalization")

        source_ref = resolve_reference_audio_source(
            reference_audio_path=reference_audio_path,
            reference_audio_url=reference_audio_url,
            reference_audio_base64=reference_audio_base64,
            work_dir=work_dir,
        )

        reference_meta: Dict[str, Any] = {
            "provided": bool(source_ref),
            "source_path": str(source_ref) if source_ref else None,
            "preprocessed_path": None,
            "voice_preset": preset_meta,
        }

        cleaned_ref_text = build_ref_text(ref_text, normalized_text, language)
        reference_quality = reference_quality_note(language, cleaned_ref_text)
        if source_ref and preprocess_reference:
            preprocessed_ref = Path(work_dir) / "reference_preprocessed.wav"
            reference_stats = preprocess_reference_audio(
                source_path=source_ref,
                output_path=preprocessed_ref,
                target_sr=self.sampling_rate,
                trim_silence=ref_trim_silence,
                trim_top_db=ref_trim_top_db,
                apply_vad=ref_apply_vad,
                vad_top_db=ref_vad_top_db,
                max_internal_silence_ms=ref_max_internal_silence_ms,
                apply_denoise=manual_overrides.get("ref_apply_denoise", True),
                denoise_strength=ref_denoise_strength,
                apply_rms_normalize=ref_apply_rms_normalize,
                target_rms_dbfs=ref_target_rms_dbfs,
                min_seconds=ref_min_seconds,
                max_seconds=ref_max_seconds,
            )
            reference_meta.update(reference_stats)
            source_ref = preprocessed_ref
        elif source_ref:
            reference_meta["preprocessed_path"] = str(source_ref)

        actual_mode = infer_mode(str(source_ref) if source_ref else None, custom_instruct, mode)
        voice_clone_prompt = None
        if actual_mode == "clone":
            if source_ref is None:
                raise ValueError("mode='clone' requires reference audio")
            voice_clone_prompt = self.create_voice_clone_prompt(
                ref_audio=str(source_ref),
                ref_text=cleaned_ref_text,
                preprocess_prompt=bool(cfg["preprocess_prompt"]),
                language=language,
            )

        chunk_infos = segment_text_with_pauses(
            normalized_text,
            int(cfg["join_silence_ms"]),
            int(cfg["max_segment_chars"]) if cfg.get("max_segment_chars") else None,
            20 if canonical_lang(language) == "my" else None,
            language,
        )
        if not chunk_infos:
            chunk_infos = [{"text": normalized_text, "pause_ms": int(cfg["join_silence_ms"])}]

        rendered_parts = []
        for chunk_info in chunk_infos:
            prepared_text = add_config_text_omni(chunk_info["text"])
            gen_kwargs: Dict[str, Any] = {
                "text": prepared_text,
                "language": language,
                "speed": float(cfg["speed"]),
                "num_step": int(cfg["num_step"]),
                "guidance_scale": float(cfg["guidance_scale"]),
                "t_shift": float(cfg["t_shift"]),
                "layer_penalty_factor": float(cfg["layer_penalty_factor"]),
                "position_temperature": float(cfg["position_temperature"]),
                "class_temperature": float(cfg["class_temperature"]),
                "denoise": bool(cfg["denoise"]),
                "preprocess_prompt": bool(cfg["preprocess_prompt"]),
                "postprocess_output": bool(cfg["postprocess_output"]),
                "audio_chunk_duration": float(cfg["audio_chunk_duration"]),
                "audio_chunk_threshold": float(cfg["audio_chunk_threshold"]),
            }

            instruct_value = (custom_instruct or cfg.get("instruct") or "").strip()
            if instruct_value:
                gen_kwargs["instruct"] = instruct_value

            if actual_mode == "clone" and voice_clone_prompt is not None:
                gen_kwargs["voice_clone_prompt"] = voice_clone_prompt

            torch = _get_torch_module()
            with torch.inference_mode():
                out = model.generate(**gen_kwargs)

            piece = np.asarray(out[0], dtype=np.float32).flatten()
            rendered_parts.append(
                {
                    "audio": piece,
                    "pause_ms": max(int(cfg.get("min_join_silence_ms", 45)), int(chunk_info["pause_ms"])),
                }
            )

        assembled_parts = []
        for index, item in enumerate(rendered_parts):
            if index > 0:
                pause_ms = int(item["pause_ms"])
                assembled_parts.append(np.zeros(int(self.sampling_rate * pause_ms / 1000.0), dtype=np.float32))
            assembled_parts.append(item["audio"].astype(np.float32, copy=False))

        combined = np.concatenate(assembled_parts, axis=0).astype(np.float32, copy=False)

        combined = self._apply_pitch_shift(combined, float(cfg["pitch_shift"]))
        trailing = np.zeros(int(self.sampling_rate * float(cfg["trailing_silence_ms"]) / 1000.0), dtype=np.float32)
        combined = np.concatenate([combined, trailing]).astype(np.float32)

        gain_ratio = 10 ** (effective_gain_db / 20.0)
        final_audio = np.clip(combined * gain_ratio, -1.0, 1.0).astype(np.float32)

        output_stub = hashlib.md5(
            json.dumps(
                {
                    "text": normalized_text,
                    "language": language,
                    "mode": actual_mode,
                    "voice_preset": voice_preset,
                    "ref_text": cleaned_ref_text,
                    "cfg": {
                        "speed": float(cfg["speed"]),
                        "pitch_shift": float(cfg["pitch_shift"]),
                        "num_step": int(cfg["num_step"]),
                        "guidance_scale": float(cfg["guidance_scale"]),
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:10]
        unique_suffix = uuid.uuid4().hex[:8]
        output_filename = output_filename or f"{language}_{actual_mode}_{output_stub}_{unique_suffix}.wav"
        output_path = self.output_dir / output_filename
        if save_output:
            write_output_wav(final_audio, self.sampling_rate, output_path)

        compute_seconds = round(time.perf_counter() - started_at, 4)
        response = {
            "ok": True,
            "mode": actual_mode,
            "language": language,
            "language_label": LANGUAGE_LABELS.get(language, language),
            "text": normalized_text,
            "ref_text": cleaned_ref_text,
            "reference_quality": reference_quality,
            "segments": [item["text"] for item in chunk_infos],
            "config": cfg,
            "effective_config": {
                "speed": float(cfg["speed"]),
                "pitch_shift": float(cfg["pitch_shift"]),
                "num_step": int(cfg["num_step"]),
                "guidance_scale": float(cfg["guidance_scale"]),
                "join_silence_ms": int(cfg["join_silence_ms"]),
                "trailing_silence_ms": int(cfg["trailing_silence_ms"]),
            },
            "ad_safe": bool(ad_safe),
            "reference": reference_meta,
            "audio_base64": encode_wav_base64(final_audio, self.sampling_rate) if return_base64 else None,
            "output_path": str(output_path) if save_output else None,
            "sampling_rate": self.sampling_rate,
            "duration_sec": round(len(final_audio) / self.sampling_rate, 4),
            "compute_seconds": compute_seconds,
            "gain_db": effective_gain_db,
            "effective_gain_db": effective_gain_db,
            "device": self.device,
        }
        if debug:
            response["voice_library"] = self.get_prompt_voice_healthcheck()
            response["model_source"] = self.model_source

        try:
            torch = _get_torch_module()
        except RuntimeError:
            torch = None
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()
        gc.collect()

        return response

def get_voice_library_status() -> Dict[str, Any]:
    return get_service().get_prompt_voice_healthcheck()

SERVICE: Optional[OmniVoiceService] = None

def get_service() -> OmniVoiceService:
    global SERVICE
    if SERVICE is None:
        SERVICE = OmniVoiceService(model_id=os.getenv("MODEL_ID", "k2-fsa/OmniVoice"))
    return SERVICE
