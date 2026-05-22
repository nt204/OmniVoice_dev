from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

from .presets import canonical_lang

def add_config_text_omni(text: str) -> str:
    return f" . {text.strip()} . "


def _replace_common_symbols(text: str) -> str:
    replacements = {
        "\u00a0": " ",
        "\u200b": " ",
        "\ufeff": " ",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "–": "-",
        "—": "-",
        "•": ". ",
        "·": ". ",
    }
    for src, dest in replacements.items():
        text = text.replace(src, dest)
    return text


def _normalize_list_breaks(text: str) -> str:
    text = re.sub(r"\s*[\r\n]+\s*", ". ", text)
    text = re.sub(r"\s*[|]\s*", " / ", text)
    return text


def _collapse_punctuation_runs(text: str, keep_chars: str) -> str:
    pattern = rf"([{re.escape(keep_chars)}])\1+"
    return re.sub(pattern, r"\1", text)


def _protect_numeric_punctuation(text: str) -> str:
    replacements = {
        ".": "__NUM_DOT__",
        ",": "__NUM_COMMA__",
        ":": "__NUM_COLON__",
        "/": "__NUM_SLASH__",
    }
    for punct, marker in replacements.items():
        text = re.sub(rf"(?<=\d){re.escape(punct)}(?=\d)", marker, text)
    return text


def _restore_numeric_punctuation(text: str) -> str:
    return (
        text.replace("__NUM_DOT__", ".")
        .replace("__NUM_COMMA__", ",")
        .replace("__NUM_COLON__", ":")
        .replace("__NUM_SLASH__", "/")
    )


def _basic_text_cleanup(text: str) -> str:
    text = unicodedata.normalize("NFC", str(text or ""))
    text = _replace_common_symbols(text)
    text = _normalize_list_breaks(text)
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_number_separators(text: str) -> str:
    text = re.sub(r"(?<=\d)\s*,\s*(?=\d{3}\b)", ",", text)
    text = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", text)
    text = re.sub(r"(?<=\d)\s*:\s*(?=\d)", ":", text)
    text = re.sub(r"(?<=\d)\s*/\s*(?=\d)", "/", text)
    return text


def _trim_punctuation_spacing(text: str, punctuation: str, protect_numeric: bool = False) -> str:
    text = re.sub(rf"\s+([{re.escape(punctuation)}])", r"\1", text)
    if protect_numeric:
        leading_safe = "".join(ch for ch in punctuation if ch in ",;!?")
        numeric_sensitive = "".join(ch for ch in punctuation if ch in ".:/")
        other_chars = "".join(ch for ch in punctuation if ch not in f"{leading_safe}{numeric_sensitive}")
        if leading_safe:
            text = re.sub(rf"([{re.escape(leading_safe)}])(\S)", r"\1 \2", text)
        if numeric_sensitive:
            text = re.sub(rf"([{re.escape(numeric_sensitive)}])(?!\d)(\S)", r"\1 \2", text)
        if other_chars:
            text = re.sub(rf"([{re.escape(other_chars)}])(\S)", r"\1 \2", text)
    else:
        text = re.sub(rf"([{re.escape(punctuation)}])(\S)", r"\1 \2", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_vietnamese_tts_text(text: str) -> str:
    text = re.sub(r"(\d+)\s*%", r"\1 phần trăm", text)
    text = re.sub(r"(\d+)\s*(?:°c|ºc)", r"\1 độ C", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d+)\s*(?:km/h|kmh)", r"\1 ki lô mét trên giờ", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d+)\s*(?:vnd|đ|₫)", r"\1 đồng", text, flags=re.IGNORECASE)
    text = re.sub(r"\bTP\.\s*HCM\b", "Thành phố Hồ Chí Minh", text, flags=re.IGNORECASE)
    text = re.sub(r"\bQ\.\s*(\d+)\b", r"quận \1", text, flags=re.IGNORECASE)
    text = _normalize_number_separators(text)
    text = re.sub(r"(?<=\w)\s*-\s*(?=\w)", "-", text)
    text = _collapse_punctuation_runs(text, ",.;:!?")
    return _trim_punctuation_spacing(text, ",.;:!?", protect_numeric=True)

def normalize_myanmar_tts_text(text: str) -> str:
    text = _normalize_number_separators(text)
    text = re.sub(r"(?<=\w)\s*-\s*(?=\w)", "-", text)
    text = re.sub(r"\s*[,;]\s*", "၊ ", text)
    text = re.sub(r"\s*[.!?]\s*", "။ ", text)
    text = _collapse_punctuation_runs(text, "၊။")
    return _trim_punctuation_spacing(text, "၊။")

def normalize_khmer_tts_text(text: str) -> str:
    text = re.sub(r"(?<=\d)\s*%\s*", " ភាគរយ", text)
    text = _normalize_number_separators(text)
    text = re.sub(r"\s*[.!?]\s*", "។ ", text)
    text = _collapse_punctuation_runs(text, "។៕,;:")
    return _trim_punctuation_spacing(text, "។៕,;:/", protect_numeric=True)

def preprocess_text_for_tts(text: str, lang: str, is_reference: bool = False) -> str:
    lang = canonical_lang(lang)
    text = _basic_text_cleanup(text)
    if not text:
        return text

    if is_reference:
        return text

    text = text.replace("…", ".")
    text = _collapse_punctuation_runs(text, ",.;:!?/|")

    if lang == "vi":
        return normalize_vietnamese_tts_text(text)
    if lang == "my":
        return normalize_myanmar_tts_text(text)
    if lang == "km":
        return normalize_khmer_tts_text(text)
    if lang in {"lo", "th"}:
        text = _trim_punctuation_spacing(text, ",.;:!?")

    return text

def clean_and_segment_text(text: str) -> List[str]:
    text = _protect_numeric_punctuation(re.sub(r"\s+", " ", str(text or "")).strip())
    punct_pattern = r"(\.\.+|…+|[.!?,;:/—–\-，。？！、；៖၊။៕])"
    parts = re.split(punct_pattern, text)
    segments: List[str] = []
    current_text = ""
    for part in parts:
        if not part:
            continue
        if re.fullmatch(punct_pattern, part):
            if current_text.strip():
                segments.append((current_text + part).strip())
                current_text = ""
        else:
            current_text += part
    if current_text.strip():
        segments.append(current_text.strip())
    return [_restore_numeric_punctuation(seg) for seg in segments if seg.strip()]

def _split_segment_to_max_chars(segment: str, max_chars: Optional[int]) -> List[str]:
    segment = _protect_numeric_punctuation((segment or "").strip())
    if not segment:
        return []
    if not max_chars or len(segment) <= max_chars:
        return [_restore_numeric_punctuation(segment)]

    sep_pattern = r"([,;:，、；៖၊。។៕])"
    tokens = re.split(sep_pattern, segment)
    parts: List[str] = []
    current = ""
    for token in tokens:
        if not token:
            continue
        candidate = f"{current}{token}".strip()
        if current and len(candidate) > max_chars:
            parts.append(current.strip())
            current = token.strip()
        else:
            current = candidate
    if current.strip():
        parts.append(current.strip())

    refined: List[str] = []
    for part in parts:
        if len(part) <= max_chars:
            refined.append(part)
            continue
        words = part.split(" ")
        current_word_chunk = ""
        for word in words:
            candidate = f"{current_word_chunk} {word}".strip()
            if current_word_chunk and len(candidate) > max_chars:
                refined.append(current_word_chunk.strip())
                current_word_chunk = word
            else:
                current_word_chunk = candidate
        if current_word_chunk.strip():
            refined.append(current_word_chunk.strip())

    final_parts: List[str] = []
    for part in refined:
        if len(part) <= max_chars:
            final_parts.append(part)
            continue
        start = 0
        while start < len(part):
            final_parts.append(part[start:start + max_chars].strip())
            start += max_chars
    return [_restore_numeric_punctuation(part) for part in final_parts if part]

def _merge_short_chunks(
    chunks: List[Dict[str, Any]],
    max_chars: Optional[int],
    min_chars: Optional[int],
) -> List[Dict[str, Any]]:
    if not chunks or not max_chars or not min_chars:
        return chunks

    merged: List[Dict[str, Any]] = []
    idx = 0
    while idx < len(chunks):
        current = dict(chunks[idx])
        current_text = str(current.get("text", "")).strip()
        if not current_text:
            idx += 1
            continue

        current["text"] = current_text
        current_len = len(current_text)
        if current_len >= min_chars:
            merged.append(current)
            idx += 1
            continue

        is_terminal_short_chunk = current_text.endswith((".", "!", "?", "။", "。", "؟", "！", "？"))

        if idx + 1 < len(chunks):
            next_chunk = dict(chunks[idx + 1])
            next_text = str(next_chunk.get("text", "")).strip()
            if next_text:
                combined = f"{current_text} {next_text}".strip()
                if len(combined) <= max_chars:
                    next_chunk["text"] = combined
                    next_chunk["pause_ms"] = int(next_chunk.get("pause_ms", current.get("pause_ms", 0)))
                    chunks[idx + 1] = next_chunk
                    idx += 1
                    continue

        if merged and not is_terminal_short_chunk:
            prev = dict(merged[-1])
            combined = f"{prev['text']} {current_text}".strip()
            if len(combined) <= max_chars:
                prev["text"] = combined
                prev["pause_ms"] = int(current.get("pause_ms", prev.get("pause_ms", 0)))
                merged[-1] = prev
                idx += 1
                continue

        merged.append(current)
        idx += 1

    return merged

def segment_text_with_pauses(
    text: str,
    join_silence_ms: int,
    max_chars: Optional[int] = None,
    min_chars: Optional[int] = None,
    lang: Optional[str] = None,
) -> List[Dict[str, Any]]:
    pause_scale = {
        ",": 0.75,
        ";": 0.95,
        ":": 1.0,
        ".": 1.15,
        "!": 1.2,
        "?": 1.2,
        "…": 1.3,
        "。": 1.15,
        "，": 0.75,
        "？": 1.2,
        "！": 1.2,
        "、": 0.75,
        "；": 0.95,
        "៖": 1.0,
    }
    if canonical_lang(lang) == "my":
        pause_scale["၊"] = 0.66
        pause_scale["။"] = 1.00
    segments = clean_and_segment_text(text)
    result: List[Dict[str, Any]] = []
    base_pause = max(40, int(join_silence_ms))
    for segment in segments:
        split_segments = _split_segment_to_max_chars(segment, max_chars)
        for idx, piece in enumerate(split_segments):
            ending = piece[-1] if piece else ""
            pause_ms = int(base_pause * pause_scale.get(ending, 0.9 if idx < len(split_segments) - 1 else 1.0))
            result.append({"text": piece, "pause_ms": pause_ms})
    result = _merge_short_chunks(result, max_chars=max_chars, min_chars=min_chars)
    if not result and text.strip():
        result.append({"text": text.strip(), "pause_ms": base_pause})
    return result

def reference_quality_note(lang: str, ref_text: Optional[str]) -> str:
    ref_text = _basic_text_cleanup(ref_text or "")
    lang = canonical_lang(lang)
    if not ref_text:
        return "missing_ref_text"
    size = len(ref_text)
    if lang == "vi":
        if size < 20:
            return "ref_text_too_short_vi"
        if size > 220:
            return "ref_text_too_long_vi"
        return "ok_vi"
    if lang == "my":
        if size < 15:
            return "ref_text_too_short_my"
        if size > 180:
            return "ref_text_too_long_my"
        return "ok_my"
    return "unchecked"


def build_ref_text(ref_text: Optional[str], fallback_text: Optional[str], lang: str) -> Optional[str]:
    raw = (ref_text or "").strip()
    if raw:
        return preprocess_text_for_tts(raw, lang, is_reference=True)
    fallback = (fallback_text or "").strip()
    if fallback:
        return preprocess_text_for_tts(fallback, lang, is_reference=True)
    return None
