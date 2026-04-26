from __future__ import annotations

import base64
import binascii
import io
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import numpy as np
import requests
import soundfile as sf

DEFAULT_TARGET_SR = 24000
EPS = 1e-8
_LIBROSA_MODULE: Any = None
_LIBROSA_IMPORT_ERROR: Optional[Exception] = None


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


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def guess_extension_from_url(url: str, default: str = ".bin") -> str:
    path = urlparse(url).path
    suffix = Path(path).suffix
    return suffix if suffix else default


def decode_base64_to_file(data: str, out_path: str | Path) -> Path:
    payload = data.split(",", 1)[1] if data.startswith("data:") and "," in data else data
    try:
        raw = base64.b64decode(payload, validate=True)
    except binascii.Error as exc:
        raise ValueError("reference_audio_base64 is not valid base64") from exc
    out = Path(out_path)
    out.write_bytes(raw)
    return out


def download_file(url: str, out_path: str | Path, timeout: int = 60) -> Path:
    out = Path(out_path)
    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        with out.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    return out


def resolve_reference_audio_source(
    *,
    reference_audio_path: Optional[str],
    reference_audio_url: Optional[str],
    reference_audio_base64: Optional[str],
    work_dir: str | Path,
) -> Optional[Path]:
    work_dir = ensure_dir(work_dir)

    if reference_audio_path:
        p = Path(reference_audio_path)
        if not p.exists():
            raise FileNotFoundError(f"reference_audio_path not found: {p}")
        return p

    if reference_audio_url:
        suffix = guess_extension_from_url(reference_audio_url, default=".bin")
        return download_file(reference_audio_url, work_dir / f"reference_from_url{suffix}")

    if reference_audio_base64:
        return decode_base64_to_file(reference_audio_base64, work_dir / "reference_from_base64.bin")

    return None


def load_audio_mono(path: str | Path, sr: Optional[int] = None) -> Tuple[np.ndarray, int]:
    librosa = _get_librosa_module()
    audio, sample_rate = librosa.load(str(path), sr=sr, mono=True)
    if audio.ndim != 1:
        audio = np.mean(audio, axis=0)
    audio = np.nan_to_num(audio).astype(np.float32)
    return audio, int(sample_rate)


def _safe_trim(audio: np.ndarray, top_db: int = 35) -> Tuple[np.ndarray, bool]:
    if audio.size == 0:
        return audio, False
    librosa = _get_librosa_module()
    trimmed, idx = librosa.effects.trim(audio, top_db=top_db)
    changed = bool(idx[0] > 0 or idx[1] < len(audio))
    if trimmed.size == 0:
        return audio, False
    return trimmed.astype(np.float32), changed


def _remove_dc_offset(audio: np.ndarray) -> np.ndarray:
    if audio.size == 0:
        return audio.astype(np.float32)
    return (audio - np.mean(audio)).astype(np.float32)


def _peak_normalize(audio: np.ndarray, target_peak: float = 0.95) -> np.ndarray:
    if audio.size == 0:
        return audio.astype(np.float32)
    peak = float(np.max(np.abs(audio)))
    if peak < EPS:
        return audio.astype(np.float32)
    if peak <= float(target_peak):
        return audio.astype(np.float32)
    return (audio / peak * float(target_peak)).astype(np.float32)


def _rms_dbfs(audio: np.ndarray) -> float:
    if audio.size == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(np.square(audio)) + EPS))
    return 20.0 * np.log10(max(rms, EPS))


def _normalize_rms(audio: np.ndarray, target_dbfs: float = -22.0, min_gain: float = 0.6, max_gain: float = 2.5) -> np.ndarray:
    if audio.size == 0:
        return audio.astype(np.float32)
    current = _rms_dbfs(audio)
    gain = 10.0 ** ((target_dbfs - current) / 20.0)
    gain = max(min_gain, min(max_gain, gain))
    return (audio * gain).astype(np.float32)


def _limit_duration(audio: np.ndarray, sr: int, max_seconds: float) -> Tuple[np.ndarray, bool]:
    max_samples = int(sr * max_seconds)
    if max_samples <= 0 or audio.size <= max_samples:
        return audio, False
    return audio[:max_samples].astype(np.float32), True


def _estimate_snr_db(audio: np.ndarray, frame_length: int = 2048, hop_length: int = 512) -> float:
    if audio.size < frame_length:
        return 0.0
    librosa = _get_librosa_module()
    rms = librosa.feature.rms(y=audio, frame_length=frame_length, hop_length=hop_length, center=True).flatten()
    if rms.size < 8:
        return 0.0
    signal = float(np.percentile(rms, 90))
    noise = float(np.percentile(rms, 20))
    if signal < EPS:
        return 0.0
    return float(20.0 * np.log10(max(signal, EPS) / max(noise, EPS)))


def _estimate_silence_ratio(audio: np.ndarray, frame_length: int = 2048, hop_length: int = 512) -> float:
    if audio.size < frame_length:
        return 0.0
    librosa = _get_librosa_module()
    intervals = librosa.effects.split(audio, top_db=35, frame_length=frame_length, hop_length=hop_length)
    if len(intervals) == 0:
        return 1.0
    voiced = sum(max(0, end - start) for start, end in intervals)
    return float(max(0.0, 1.0 - voiced / max(len(audio), 1)))


def analyze_reference_audio(audio: np.ndarray, sr: int) -> Dict[str, Any]:
    duration_sec = float(len(audio) / max(sr, 1))
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    clipping_ratio = float(np.mean(np.abs(audio) >= 0.995)) if audio.size else 0.0
    rms_dbfs = _rms_dbfs(audio)
    snr_db = _estimate_snr_db(audio)
    silence_ratio = _estimate_silence_ratio(audio)
    if audio.size:
        librosa = _get_librosa_module()
        zero_crossing_rate = float(np.mean(librosa.feature.zero_crossing_rate(y=audio)))
    else:
        zero_crossing_rate = 0.0

    warnings = []
    if duration_sec < 1.5:
        warnings.append("reference_audio_short")
    if clipping_ratio > 0.001:
        warnings.append("reference_audio_possible_clipping")
    if rms_dbfs < -36.0:
        warnings.append("reference_audio_too_quiet")
    if snr_db < 10.0:
        warnings.append("reference_audio_low_snr")
    if silence_ratio > 0.35:
        warnings.append("reference_audio_contains_too_much_silence")

    return {
        "duration_sec": round(duration_sec, 4),
        "peak": round(peak, 6),
        "rms_dbfs": round(rms_dbfs, 3),
        "estimated_snr_db": round(snr_db, 3),
        "silence_ratio": round(silence_ratio, 4),
        "clipping_ratio": round(clipping_ratio, 6),
        "zero_crossing_rate": round(zero_crossing_rate, 6),
        "warnings": warnings,
    }


def _keep_voiced_regions(
    audio: np.ndarray,
    sr: int,
    *,
    top_db: int = 32,
    frame_length: int = 2048,
    hop_length: int = 512,
    keep_silence_ms: int = 120,
) -> Tuple[np.ndarray, bool]:
    if audio.size == 0:
        return audio.astype(np.float32), False

    librosa = _get_librosa_module()
    intervals = librosa.effects.split(audio, top_db=top_db, frame_length=frame_length, hop_length=hop_length)
    if len(intervals) == 0:
        return audio.astype(np.float32), False
    if len(intervals) == 1 and intervals[0][0] == 0 and intervals[0][1] >= len(audio):
        return audio.astype(np.float32), False

    gap = np.zeros(int(sr * max(0, keep_silence_ms) / 1000.0), dtype=np.float32)
    pieces = []
    for start, end in intervals:
        piece = audio[start:end].astype(np.float32)
        if piece.size:
            pieces.append(piece)
    if not pieces:
        return audio.astype(np.float32), False

    if len(pieces) == 1:
        rebuilt = pieces[0]
    else:
        joined_pieces = []
        for idx, piece in enumerate(pieces):
            if idx > 0 and gap.size:
                joined_pieces.append(gap)
            joined_pieces.append(piece)
        rebuilt = np.concatenate(joined_pieces, axis=0)
    return rebuilt.astype(np.float32), True


def _spectral_denoise(audio: np.ndarray, sr: int, strength: float = 0.18) -> Tuple[np.ndarray, bool]:
    if audio.size < 2048 or strength <= 0:
        return audio.astype(np.float32), False

    librosa = _get_librosa_module()
    n_fft = 1024
    hop_length = 256
    stft = librosa.stft(audio.astype(np.float32), n_fft=n_fft, hop_length=hop_length)
    mag, phase = np.abs(stft), np.angle(stft)
    if mag.size == 0:
        return audio.astype(np.float32), False

    frame_energy = np.mean(mag, axis=0)
    if frame_energy.size < 8:
        return audio.astype(np.float32), False

    noise_threshold = np.percentile(frame_energy, 20)
    noise_frames = mag[:, frame_energy <= noise_threshold]
    if noise_frames.size == 0:
        return audio.astype(np.float32), False

    noise_profile = np.mean(noise_frames, axis=1, keepdims=True)
    floor = 0.10
    suppression = np.maximum(floor, 1.0 - float(strength) * noise_profile / np.maximum(mag, EPS))
    clean_mag = mag * suppression
    clean_stft = clean_mag * np.exp(1j * phase)
    out = librosa.istft(clean_stft, hop_length=hop_length, length=len(audio))
    return out.astype(np.float32), True


def preprocess_reference_audio(
    source_path: str | Path,
    output_path: str | Path,
    *,
    target_sr: int = DEFAULT_TARGET_SR,
    trim_silence: bool = True,
    trim_top_db: int = 35,
    apply_vad: bool = True,
    vad_top_db: int = 35,
    max_internal_silence_ms: int = 120,
    apply_denoise: bool = True,
    denoise_strength: float = 0.18,
    apply_rms_normalize: bool = True,
    target_rms_dbfs: float = -22.0,
    peak_normalize: bool = True,
    peak_target: float = 0.92,
    min_seconds: float = 1.5,
    max_seconds: float = 10.0,
) -> Dict[str, Any]:
    raw_audio, original_sr = load_audio_mono(source_path, sr=None)
    quality_before = analyze_reference_audio(raw_audio, original_sr)
    duration_before = float(len(raw_audio) / max(original_sr, 1))

    processed = raw_audio.astype(np.float32, copy=False)
    if original_sr != target_sr:
        librosa = _get_librosa_module()
        processed = librosa.resample(processed, orig_sr=original_sr, target_sr=target_sr).astype(np.float32)

    trimmed_silence_applied = False
    vad_applied = False
    denoise_applied = False
    rms_applied = False
    capped_to_max_duration = False
    fell_back_to_resampled = False

    processed = _remove_dc_offset(processed)

    if trim_silence:
        processed, trimmed_silence_applied = _safe_trim(processed, top_db=trim_top_db)

    if apply_vad:
        processed, vad_applied = _keep_voiced_regions(
            processed,
            target_sr,
            top_db=vad_top_db,
            keep_silence_ms=max_internal_silence_ms,
        )

    if apply_denoise:
        processed, denoise_applied = _spectral_denoise(processed, target_sr, strength=denoise_strength)

    processed = _remove_dc_offset(processed)

    if apply_rms_normalize:
        processed = _normalize_rms(processed, target_dbfs=target_rms_dbfs)
        rms_applied = True

    if peak_normalize:
        processed = _peak_normalize(processed, target_peak=peak_target)

    processed, capped_to_max_duration = _limit_duration(processed, target_sr, max_seconds=max_seconds)

    duration_after = float(len(processed) / max(target_sr, 1))
    if duration_after < float(min_seconds):
        # Over-aggressive cleanup can destroy prompt quality for voice cloning.
        # Fall back to a simpler resampled version instead of shipping a broken prompt.
        processed = raw_audio.astype(np.float32, copy=False)
        if original_sr != target_sr:
            librosa = _get_librosa_module()
            processed = librosa.resample(processed, orig_sr=original_sr, target_sr=target_sr).astype(np.float32)
        processed = _remove_dc_offset(processed)
        if apply_rms_normalize:
            processed = _normalize_rms(processed, target_dbfs=target_rms_dbfs)
            rms_applied = True
        if peak_normalize:
            processed = _peak_normalize(processed, target_peak=peak_target)
        processed, capped_to_max_duration = _limit_duration(processed, target_sr, max_seconds=max_seconds)
        fell_back_to_resampled = True

    duration_after = float(len(processed) / max(target_sr, 1))
    quality_after = analyze_reference_audio(processed, target_sr)
    if fell_back_to_resampled:
        quality_after["warnings"].append("preprocess_fallback_to_resampled_reference")

    out = Path(output_path)
    sf.write(str(out), processed, target_sr, subtype="PCM_16")

    return {
        "path": str(out),
        "original_sample_rate": int(original_sr),
        "sample_rate": int(target_sr),
        "duration_before_sec": round(duration_before, 4),
        "duration_after_sec": round(duration_after, 4),
        "trimmed_silence": trimmed_silence_applied,
        "applied_vad": vad_applied,
        "applied_denoise": denoise_applied,
        "applied_rms_normalize": rms_applied,
        "capped_to_max_duration": capped_to_max_duration,
        "fell_back_to_resampled_reference": fell_back_to_resampled,
        "channels": 1,
        "quality_before": quality_before,
        "quality_after": quality_after,
    }


def wav_bytes_from_array(audio: np.ndarray, sample_rate: int) -> bytes:
    with io.BytesIO() as buf:
        sf.write(buf, audio.astype(np.float32), sample_rate, format="WAV", subtype="PCM_16")
        return buf.getvalue()


def encode_wav_base64(audio: np.ndarray, sample_rate: int) -> str:
    return base64.b64encode(wav_bytes_from_array(audio, sample_rate)).decode("utf-8")


def write_output_wav(audio: np.ndarray, sample_rate: int, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    ensure_dir(output_path.parent)
    sf.write(str(output_path), audio.astype(np.float32), sample_rate, subtype="PCM_16")
    return output_path
