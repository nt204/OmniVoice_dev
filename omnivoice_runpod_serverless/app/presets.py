from __future__ import annotations

import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_OMNI_CONFIG: Dict[str, Any] = {
    "num_step": 32,
    "guidance_scale": 3.8,
    "t_shift": 0.1,
    "layer_penalty_factor": 10.0,
    "position_temperature": 0.0,
    "class_temperature": 0.0,
    "denoise": True,
    "preprocess_prompt": True,
    "postprocess_output": False,
    "audio_chunk_duration": 0.0,
    "audio_chunk_threshold": 60.0,
    "speed": 1.0,
    "pitch_shift": 1.0,
    "join_silence_ms": 120,
    "trailing_silence_ms": 250,
    "min_join_silence_ms": 45,
    "max_segment_chars": None,
    "instruct": None,
}

LANGUAGE_LABELS: Dict[str, str] = {
    "en": "English",
    "vi": "Vietnamese",
    "lo": "Lao",
    "km": "Khmer",
    "th": "Thai",
    "my": "Myanmar",
}

LANGUAGE_PRESETS: Dict[str, Dict[str, Any]] = {
    "en": {"label": "English", "instruct": None},
    "vi": {"label": "Vietnamese", "instruct": "male, young adult"},
    "lo": {"label": "Lao", "instruct": "female, moderate pitch"},
    "km": {"label": "Khmer", "instruct": "male, moderate pitch"},
    "th": {"label": "Thai", "instruct": "female, high pitch"},
    "my": {"label": "Myanmar", "instruct": "female, young adult"},
}

DEPLOY_LANGUAGE_CONFIGS: Dict[str, Dict[str, Any]] = {
    "en": {
        "num_step": 32,
        "guidance_scale": 2.5,
        "speed": 0.97,
        "join_silence_ms": 70,
        "trailing_silence_ms": 80,
        "min_join_silence_ms": 0,
        "max_segment_chars": 220,
    },
    "vi": {
        "num_step": 32,
        "guidance_scale": 3.9,
        "speed": 1.0,
        "pitch_shift": 1.0,
        "join_silence_ms": 110,
        "trailing_silence_ms": 220,
        "preprocess_prompt": True,
        "max_segment_chars": 160,
    },
    "lo": {
        "num_step": 32,
        "guidance_scale": 3.8,
        "speed": 1.0,
        "pitch_shift": 1.0,
        "join_silence_ms": 130,
        "trailing_silence_ms": 260,
        "preprocess_prompt": True,
    },
    "km": {
        "num_step": 32,
        "guidance_scale": 3.8,
        "speed": 1.0,
        "pitch_shift": 1.0,
        "join_silence_ms": 130,
        "trailing_silence_ms": 260,
        "preprocess_prompt": True,
    },
    "th": {
        "num_step": 32,
        "guidance_scale": 3.9,
        "speed": 1.0,
        "pitch_shift": 1.0,
        "join_silence_ms": 120,
        "trailing_silence_ms": 240,
        "preprocess_prompt": True,
    },
    "my": {
        "num_step": 36,
        "guidance_scale": 4.0,
        "speed": 1.0,
        "pitch_shift": 1.0,
        "join_silence_ms": 80,
        "trailing_silence_ms": 150,
        "preprocess_prompt": True,
        "max_segment_chars": 90,
    },
}
EMOTION_PRESETS_BY_LANG: Dict[str, Dict[str, Dict[str, Any]]] = {
    "en": {
        "Mặc định": {
            "overrides": {},
            "gain_db": 0.0,
        },

        "Vui vẻ (Happy)": {
            "overrides": {
                "pitch_shift": 1.006,
                "speed": 1.025,
                "join_silence_ms": 88,
                "trailing_silence_ms": 185,
            },
            "gain_db": 0.25,
        },

        "Buồn bã (Sad)": {
            "overrides": {
                "pitch_shift": 0.992,
                "speed": 0.94,
                "join_silence_ms": 125,
                "trailing_silence_ms": 310,
            },
            "gain_db": -0.45,
        },

        "Hào hứng (Excited)": {
            "overrides": {
                "pitch_shift": 1.01,
                "speed": 1.045,
                "join_silence_ms": 76,
                "trailing_silence_ms": 165,
                "guidance_scale": 3.75,
            },
            "gain_db": 0.42,
        },

        "Giận dữ (Angry)": {
            "overrides": {
                "pitch_shift": 0.996,
                "speed": 1.035,
                "join_silence_ms": 74,
                "trailing_silence_ms": 160,
                "guidance_scale": 3.78,
            },
            "gain_db": 0.35,
        },

        "Nhẹ nhàng (Gentle)": {
            "overrides": {
                "pitch_shift": 1.0,
                "speed": 0.965,
                "join_silence_ms": 112,
                "trailing_silence_ms": 260,
            },
            "gain_db": -0.2,
        },
    },

    "vi": {
        "Mặc định": {
            "overrides": {},
            "gain_db": 0.0,
        },

        # Vui: nhanh hơn nhẹ, sáng hơn nhẹ, không đẩy gain quá cao
        "Vui vẻ (Happy)": {
            "overrides": {
                "pitch_shift": 1.01,
                "speed": 1.045,
                "join_silence_ms": 100,
                "trailing_silence_ms": 190,
            },
            "gain_db": 0.55,
        },

        # Buồn: chậm, ngắt dài hơn, giảm gain để giọng mềm và trầm hơn
        "Buồn bã (Sad)": {
            "overrides": {
                "pitch_shift": 0.985,
                "speed": 0.92,
                "join_silence_ms": 165,
                "trailing_silence_ms": 430,
            },
            "gain_db": -1.1,
        },

        # Hào hứng: nhanh rõ nhưng không pitch quá cao để tránh rè
        "Hào hứng (Excited)": {
            "overrides": {
                "pitch_shift": 1.015,
                "speed": 1.075,
                "join_silence_ms": 80,
                "trailing_silence_ms": 155,
            },
            "gain_db": 0.8,
        },

        # Giận: tốc độ nhanh, khoảng nghỉ ngắn, pitch hơi thấp để chắc giọng
        "Giận dữ (Angry)": {
            "overrides": {
                "pitch_shift": 0.985,
                "speed": 1.06,
                "join_silence_ms": 75,
                "trailing_silence_ms": 145,
            },
            "gain_db": 0.75,
        },

        # Nhẹ nhàng: chậm, nghỉ mềm, gain âm nhẹ
        "Nhẹ nhàng (Gentle)": {
            "overrides": {
                "pitch_shift": 1.0,
                "speed": 0.94,
                "join_silence_ms": 140,
                "trailing_silence_ms": 310,
            },
            "gain_db": -0.55,
        },
    },

    "lo": {
        "Mặc định": {
            "overrides": {
                "pitch_shift": 1.0,
                "speed": 0.985,
                "join_silence_ms": 136,
                "trailing_silence_ms": 268,
                "num_step": 32,
                "guidance_scale": 4.0,
            },
            "gain_db": 0.0,
        },

        "Vui vẻ (Happy)": {
            "overrides": {
                "pitch_shift": 1.0,
                "speed": 1.005,
                "join_silence_ms": 128,
                "trailing_silence_ms": 248,
                "num_step": 32,
                "guidance_scale": 3.95,
            },
            "gain_db": 0.18,
        },

        "Ngạc nhiên (Surprised)": {
            "overrides": {
                "pitch_shift": 1.0,
                "speed": 1.012,
                "join_silence_ms": 118,
                "trailing_silence_ms": 230,
                "num_step": 32,
                "guidance_scale": 4.05,
            },
            "gain_db": 0.22,
        },

        "Buồn bã (Sad)": {
            "overrides": {
                "pitch_shift": 1.0,
                "speed": 0.94,
                "join_silence_ms": 155,
                "trailing_silence_ms": 310,
                "num_step": 32,
                "guidance_scale": 4.15,
            },
            "gain_db": -0.45,
        },

        "Hào hứng (Excited)": {
            "overrides": {
                "pitch_shift": 1.0,
                "speed": 1.018,
                "join_silence_ms": 116,
                "trailing_silence_ms": 226,
                "num_step": 32,
                "guidance_scale": 3.9,
            },
            "gain_db": 0.28,
        },

        "Giận dữ (Angry)": {
            "overrides": {
                "pitch_shift": 1.0,
                "speed": 1.012,
                "join_silence_ms": 118,
                "trailing_silence_ms": 232,
                "num_step": 32,
                "guidance_scale": 4.05,
            },
            "gain_db": 0.24,
        },

        "Nhẹ nhàng (Gentle)": {
            "overrides": {
                "pitch_shift": 1.0,
                "speed": 0.955,
                "join_silence_ms": 150,
                "trailing_silence_ms": 300,
                "num_step": 32,
                "guidance_scale": 4.12,
            },
            "gain_db": -0.22,
        },
    },

    "my": {
        "Mặc định": {
            "overrides": {},
            "gain_db": 0.0,
        },

        "Vui vẻ (Happy)": {
            "overrides": {
                "pitch_shift": 1.01,
                "speed": 1.035,
                "join_silence_ms": 108,
                "trailing_silence_ms": 198,
            },
            "gain_db": 0.42,
        },

        "Buồn bã (Sad)": {
            "overrides": {
                "pitch_shift": 0.99,
                "speed": 0.92,
                "join_silence_ms": 170,
                "trailing_silence_ms": 440,
            },
            "gain_db": -1.0,
        },

        "Hào hứng (Excited)": {
            "overrides": {
                "pitch_shift": 1.015,
                "speed": 1.06,
                "join_silence_ms": 88,
                "trailing_silence_ms": 165,
            },
            "gain_db": 0.65,
        },

        "Giận dữ (Angry)": {
            "overrides": {
                "pitch_shift": 0.99,
                "speed": 1.05,
                "join_silence_ms": 82,
                "trailing_silence_ms": 160,
            },
            "gain_db": 0.62,
        },

        "Nhẹ nhàng (Gentle)": {
            "overrides": {
                "pitch_shift": 1.0,
                "speed": 0.94,
                "join_silence_ms": 145,
                "trailing_silence_ms": 315,
            },
            "gain_db": -0.45,
        },
    },

    "km": {
        "Mặc định": {
            "overrides": {
                "pitch_shift": 1.0,
                "speed": 0.975,
                "join_silence_ms": 142,
                "trailing_silence_ms": 286,
                "num_step": 36,
                "guidance_scale": 4.12,
                "max_segment_chars": 58,
            },
            "gain_db": 0.0,
        },

        "Vui vẻ (Happy)": {
            "overrides": {
                "pitch_shift": 1.0,
                "speed": 0.99,
                "join_silence_ms": 134,
                "trailing_silence_ms": 262,
                "num_step": 36,
                "guidance_scale": 4.02,
                "max_segment_chars": 56,
            },
            "gain_db": 0.16,
        },

        "Ngạc nhiên (Surprised)": {
            "overrides": {
                "pitch_shift": 1.0,
                "speed": 0.995,
                "join_silence_ms": 128,
                "trailing_silence_ms": 252,
                "num_step": 36,
                "guidance_scale": 4.14,
                "max_segment_chars": 54,
            },
            "gain_db": 0.18,
        },

        "Buồn bã (Sad)": {
            "overrides": {
                "pitch_shift": 1.0,
                "speed": 0.935,
                "join_silence_ms": 158,
                "trailing_silence_ms": 320,
                "num_step": 36,
                "guidance_scale": 4.22,
                "max_segment_chars": 60,
            },
            "gain_db": -0.38,
        },

        "Hào hứng (Excited)": {
            "overrides": {
                "pitch_shift": 1.0,
                "speed": 1.0,
                "join_silence_ms": 124,
                "trailing_silence_ms": 242,
                "num_step": 36,
                "guidance_scale": 3.98,
                "max_segment_chars": 54,
            },
            "gain_db": 0.24,
        },

        "Giận dữ (Angry)": {
            "overrides": {
                "pitch_shift": 1.0,
                "speed": 0.998,
                "join_silence_ms": 122,
                "trailing_silence_ms": 238,
                "num_step": 36,
                "guidance_scale": 4.12,
                "max_segment_chars": 54,
            },
            "gain_db": 0.22,
        },

        "Nhẹ nhàng (Gentle)": {
            "overrides": {
                "pitch_shift": 1.0,
                "speed": 0.955,
                "join_silence_ms": 152,
                "trailing_silence_ms": 304,
                "num_step": 36,
                "guidance_scale": 4.18,
                "max_segment_chars": 60,
            },
            "gain_db": -0.2,
        },
    },
}


AD_PRESETS_BY_LANG: Dict[str, Dict[str, Dict[str, Any]]] = {
    "en": {
        "Không bổ trợ": {
            "overrides": {},
            "gain_db": 0.0,
        },

        "Cường điệu rất nhẹ": {
            "overrides": {
                "speed": 1.008,
                "pitch_shift": 1.0,
                "join_silence_ms": 88,
                "trailing_silence_ms": 195,
                "guidance_scale": 3.68,
            },
            "gain_db": 0.08,
        },

        "Cường điệu nhẹ": {
            "overrides": {
                "speed": 1.018,
                "pitch_shift": 1.002,
                "join_silence_ms": 82,
                "trailing_silence_ms": 178,
                "guidance_scale": 3.72,
            },
            "gain_db": 0.16,
        },

        "Cường điệu vừa": {
            "overrides": {
                "speed": 1.03,
                "pitch_shift": 1.004,
                "join_silence_ms": 74,
                "trailing_silence_ms": 160,
                "guidance_scale": 3.78,
            },
            "gain_db": 0.25,
        },

        "Cường điệu mạnh": {
            "overrides": {
                "speed": 1.04,
                "pitch_shift": 1.006,
                "join_silence_ms": 68,
                "trailing_silence_ms": 145,
                "guidance_scale": 3.85,
            },
            "gain_db": 0.34,
        },
    },

    "vi": {
        "Không bổ trợ": {
            "overrides": {},
            "gain_db": 0.0,
        },

        "Cường điệu rất nhẹ": {
            "overrides": {
                "speed": 1.015,
                "pitch_shift": 1.0,
                "join_silence_ms": 105,
                "trailing_silence_ms": 205,
            },
            "gain_db": 0.18,
        },

        "Cường điệu nhẹ": {
            "overrides": {
                "speed": 1.035,
                "pitch_shift": 1.005,
                "join_silence_ms": 92,
                "trailing_silence_ms": 180,
            },
            "gain_db": 0.38,
        },

        "Cường điệu vừa": {
            "overrides": {
                "speed": 1.055,
                "pitch_shift": 1.01,
                "join_silence_ms": 82,
                "trailing_silence_ms": 160,
            },
            "gain_db": 0.62,
        },

        "Cường điệu mạnh": {
            "overrides": {
                "speed": 1.075,
                "pitch_shift": 1.012,
                "join_silence_ms": 72,
                "trailing_silence_ms": 145,
            },
            "gain_db": 0.85,
        },
    },

    "lo": {
        "Không bổ trợ": {
            "overrides": {},
            "gain_db": 0.0,
        },

        "Cường điệu rất nhẹ": {
            "overrides": {
                "speed": 0.995,
                "pitch_shift": 1.0,
                "join_silence_ms": 136,
                "trailing_silence_ms": 268,
                "num_step": 32,
                "guidance_scale": 4.0,
            },
            "gain_db": 0.06,
        },

        "Cường điệu nhẹ": {
            "overrides": {
                "speed": 1.0,
                "pitch_shift": 1.0,
                "join_silence_ms": 132,
                "trailing_silence_ms": 260,
                "num_step": 32,
                "guidance_scale": 3.96,
            },
            "gain_db": 0.12,
        },

        "Cường điệu vừa": {
            "overrides": {
                "speed": 1.008,
                "pitch_shift": 1.0,
                "join_silence_ms": 126,
                "trailing_silence_ms": 248,
                "num_step": 32,
                "guidance_scale": 3.92,
            },
            "gain_db": 0.2,
        },

        "Cường điệu mạnh": {
            "overrides": {
                "speed": 1.015,
                "pitch_shift": 1.0,
                "join_silence_ms": 120,
                "trailing_silence_ms": 238,
                "num_step": 32,
                "guidance_scale": 3.88,
            },
            "gain_db": 0.28,
        },
    },

    "my": {
        "Không bổ trợ": {
            "overrides": {},
            "gain_db": 0.0,
        },

        "Cường điệu rất nhẹ": {
            "overrides": {
                "speed": 1.01,
                "pitch_shift": 1.0,
                "join_silence_ms": 112,
                "trailing_silence_ms": 215,
            },
            "gain_db": 0.14,
        },

        "Cường điệu nhẹ": {
            "overrides": {
                "speed": 1.03,
                "pitch_shift": 1.005,
                "join_silence_ms": 98,
                "trailing_silence_ms": 188,
            },
            "gain_db": 0.32,
        },

        "Cường điệu vừa": {
            "overrides": {
                "speed": 1.05,
                "pitch_shift": 1.01,
                "join_silence_ms": 84,
                "trailing_silence_ms": 164,
            },
            "gain_db": 0.52,
        },

        "Cường điệu mạnh": {
            "overrides": {
                "speed": 1.065,
                "pitch_shift": 1.012,
                "join_silence_ms": 76,
                "trailing_silence_ms": 150,
            },
            "gain_db": 0.72,
        },
    },

    "km": {
        "Không bổ trợ": {
            "overrides": {},
            "gain_db": 0.0,
        },

        "Cường điệu rất nhẹ": {
            "overrides": {
                "speed": 0.985,
                "pitch_shift": 1.0,
                "join_silence_ms": 138,
                "trailing_silence_ms": 272,
                "num_step": 36,
                "guidance_scale": 4.12,
                "max_segment_chars": 58,
            },
            "gain_db": 0.04,
        },

        "Cường điệu nhẹ": {
            "overrides": {
                "speed": 0.992,
                "pitch_shift": 1.0,
                "join_silence_ms": 132,
                "trailing_silence_ms": 260,
                "num_step": 36,
                "guidance_scale": 4.08,
                "max_segment_chars": 56,
            },
            "gain_db": 0.1,
        },

        "Cường điệu vừa": {
            "overrides": {
                "speed": 1.0,
                "pitch_shift": 1.0,
                "join_silence_ms": 126,
                "trailing_silence_ms": 248,
                "num_step": 36,
                "guidance_scale": 4.02,
                "max_segment_chars": 54,
            },
            "gain_db": 0.16,
        },

        "Cường điệu mạnh": {
            "overrides": {
                "speed": 1.006,
                "pitch_shift": 1.0,
                "join_silence_ms": 120,
                "trailing_silence_ms": 238,
                "num_step": 36,
                "guidance_scale": 3.98,
                "max_segment_chars": 52,
            },
            "gain_db": 0.24,
        },
    },
}

LANGUAGE_ALIASES: Dict[str, str] = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "vi": "vi",
    "vn": "vi",
    "lo": "lo",
    "la": "lo",
    "km": "km",
    "kh": "km",
    "khmer": "km",
    "th": "th",
    "thai": "th",
    "my": "my",
    "mm": "my",
    "myanmar": "my",
}

PROMPT_TEXT_FILES: Dict[str, tuple[str, str]] = {
    "en": ("english_prompt_voice", "english_prompt.txt"),
    "vi": ("vietnam_prompt_voice", "vietnam_prompt.txt"),
    "km": ("khmer_prompt_voice", "khmer_prompt.txt"),
    "my": ("myanmar_prompt_voice", "myanmar_prompt.txt"),
}

VOICE_DEFINITIONS: Dict[str, List[Dict[str, Any]]] = {
    "en": [
        {"preset_key": "en_male_1", "label": "male_1", "folder": "english_prompt_voice", "audio_file": "male_1.mp3", "style_tags": ["male", "natural", "conversational"], "aliases": ["male_1"]},
        {"preset_key": "en_male_2", "label": "male_2", "folder": "english_prompt_voice", "audio_file": "male_2.mp3", "style_tags": ["male", "radio", "energetic"], "aliases": ["male_2"]},
        {"preset_key": "en_male_3", "label": "male_3", "folder": "english_prompt_voice", "audio_file": "male_3.mp3", "style_tags": ["male", "plain", "natural"], "aliases": ["male_3"]},
        {"preset_key": "en_female_1", "label": "female_1", "folder": "english_prompt_voice", "audio_file": "female_1.mp3", "style_tags": ["female", "bright", "friendly"], "aliases": ["female_1"]},
        {"preset_key": "en_female_2", "label": "female_2", "folder": "english_prompt_voice", "audio_file": "female_2.mp3", "style_tags": ["female", "news", "social"], "aliases": ["female_2"]},
        {"preset_key": "en_female_3", "label": "female_3", "folder": "english_prompt_voice", "audio_file": "female_3.mp3", "style_tags": ["female", "commercial", "clean"], "aliases": ["female_3"]},
        {"preset_key": "en_female_4", "label": "female_4", "folder": "english_prompt_voice", "audio_file": "female_4.mp3", "style_tags": ["female", "lifestyle", "upbeat"], "aliases": ["female_4"]},
    ],
    "vi": [
        {"preset_key": "vi_nam_ke_chuyen", "label": "Giọng nam kể chuyện", "folder": "vietnam_prompt_voice", "audio_file": "giong_nam_ke_chuyen.mp3", "style_tags": ["storytelling", "warm", "soft"], "aliases": ["giong_nam_ke_chuyen"]},
        {"preset_key": "vi_nam_qc", "label": "Giọng nam quảng cáo", "folder": "vietnam_prompt_voice", "audio_file": "giong_nam_qc.mp3", "style_tags": ["advertising", "bright", "decisive"], "aliases": ["giong_nam_qc"]},
        {"preset_key": "vi_nam_truong_thanh", "label": "Giọng nam trưởng thành", "folder": "vietnam_prompt_voice", "audio_file": "giong_nam_truong_thanh.mp3", "style_tags": ["adult", "stable", "balanced"], "aliases": ["giong_nam_truong_thanh"]},
        {"preset_key": "vi_nu_ke_chuyen", "label": "Giọng nữ kể chuyện", "folder": "vietnam_prompt_voice", "audio_file": "giong_nu_ke_chuyen.mp3", "style_tags": ["storytelling", "female", "soft"], "aliases": ["giong_nu_ke_chuyen"]},
        {"preset_key": "vi_nu_qc", "label": "Giọng nữ quảng cáo", "folder": "vietnam_prompt_voice", "audio_file": "giong_nu_qc.mp3", "style_tags": ["advertising", "female", "clear"], "aliases": ["giong_nu_qc"]},
        {"preset_key": "vi_tre_em_qc", "label": "Giọng trẻ em quảng cáo", "folder": "vietnam_prompt_voice", "audio_file": "giong_tre_em_qc.wav", "style_tags": ["advertising", "child", "playful"], "aliases": ["giong_tre_em_qc"]},
    ],
    "lo": [],
    "km": [
        {"preset_key": "km_1", "label": "Khmer 1", "folder": "khmer_prompt_voice", "audio_file": "1_khmer_audio_prompt.wav", "style_tags": ["neutral", "general"], "aliases": ["1_khmer_audio_prompt"]},
        {"preset_key": "km_2", "label": "Khmer 2", "folder": "khmer_prompt_voice", "audio_file": "2_khmer_audio_prompt.wav", "style_tags": ["gentle", "natural"], "aliases": ["2_khmer_audio_prompt"]},
        {"preset_key": "km_3", "label": "Khmer 3", "folder": "khmer_prompt_voice", "audio_file": "3_khmer_audio_prompt.wav", "style_tags": ["advertising", "energetic"], "aliases": ["3_khmer_audio_prompt"]},
        {"preset_key": "km_4", "label": "Khmer 4", "folder": "khmer_prompt_voice", "audio_file": "4_khmer_audio_prompt.wav", "style_tags": ["stable", "serious"], "aliases": ["4_khmer_audio_prompt"]},
    ],
    "th": [],
    "my": [
        {"preset_key": "my_1", "label": "female_rank_1", "folder": "myanmar_prompt_voice", "audio_file": "female_rank_1.wav", "style_tags": ["female", "neutral"], "aliases": ["female_rank_1", "1_myanmar_audio_prompt"]},
        {"preset_key": "my_2", "label": "female_rank_2", "folder": "myanmar_prompt_voice", "audio_file": "female_rank_2.wav", "style_tags": ["female", "gentle"], "aliases": ["female_rank_2", "2_myanmar_audio_prompt"]},
        {"preset_key": "my_3", "label": "male_rank_1", "folder": "myanmar_prompt_voice", "audio_file": "male_rank_1.wav", "style_tags": ["male", "energetic"], "aliases": ["male_rank_1", "3_myanmar_audio_prompt"]},
        {"preset_key": "my_4", "label": "male_rank_2", "folder": "myanmar_prompt_voice", "audio_file": "male_rank_2.wav", "style_tags": ["male", "balanced"], "aliases": ["male_rank_2", "4_myanmar_audio_prompt"]},
        {"preset_key": "my_5", "label": "elder_female_02", "folder": "myanmar_prompt_voice", "audio_file": "elder_female_02.wav", "style_tags": ["female", "elder"], "aliases": ["elder_female_02"]},
        {"preset_key": "my_6", "label": "elder_male_50_60", "folder": "myanmar_prompt_voice", "audio_file": "elder_male_50_60.wav", "style_tags": ["male", "elder"], "aliases": ["elder_male_50_60"]},
        {"preset_key": "my_7", "label": "elder_male_60_70", "folder": "myanmar_prompt_voice", "audio_file": "elder_male_60_70.wav", "style_tags": ["male", "elder"], "aliases": ["elder_male_60_70"]},
        {"preset_key": "my_8", "label": "elder_female_40_50", "folder": "myanmar_prompt_voice", "audio_file": "elder_female_40_50.wav", "style_tags": ["female", "elder"], "aliases": ["elder_female_40_50"]},
    ],
}


def canonical_lang(lang: Optional[str]) -> str:
    key = str(lang or "").strip().lower()
    return LANGUAGE_ALIASES.get(key, key)


def _looks_like_prompt_root(path: Path) -> bool:
    expected = ("english_prompt_voice", "vietnam_prompt_voice", "khmer_prompt_voice", "myanmar_prompt_voice")
    return path.exists() and path.is_dir() and any((path / name).exists() for name in expected)


def _find_prompt_root() -> Optional[Path]:
    env_root = os.getenv("PROMPT_VOICE_ROOT")
    direct_candidates = [
        Path(env_root).expanduser() if env_root else None,
        Path("/runpod-volume/prompt_voices"),
        Path("/app/prompt_voices"),
        Path(__file__).resolve().parents[1] / "prompt_voices",
        Path.cwd() / "prompt_voices",
    ]
    for candidate in direct_candidates:
        if candidate and _looks_like_prompt_root(candidate):
            return candidate.resolve()
    return None


PROMPT_VOICE_ROOT = _find_prompt_root()


def _read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _prompt_text_path(lang: str) -> Optional[Path]:
    lang = canonical_lang(lang)
    if not PROMPT_VOICE_ROOT or lang not in PROMPT_TEXT_FILES:
        return None
    folder, filename = PROMPT_TEXT_FILES[lang]
    return PROMPT_VOICE_ROOT / folder / filename


def _prompt_text(lang: str) -> str:
    path = _prompt_text_path(lang)
    return _read_text_if_exists(path) if path else ""


PROMPT_META_KEY_MAP: Dict[str, str] = {
    "speed": "speed",
    "numstep": "num_step",
    "num_step": "num_step",
    "guidance": "guidance_scale",
    "guidancescale": "guidance_scale",
    "guidance_scale": "guidance_scale",
    "pitch": "pitch_shift",
    "pitchshift": "pitch_shift",
    "pitch_shift": "pitch_shift",
}


def _clean_prompt_meta_key(raw_key: str) -> Optional[str]:
    normalized = "".join(ch for ch in str(raw_key or "").strip().lower() if ch.isalnum() or ch == "_")
    return PROMPT_META_KEY_MAP.get(normalized)


def _parse_prompt_meta_token(token: str) -> Optional[tuple[str, Any]]:
    match = re.match(r"^\s*([A-Za-z_]+)\s*[:=]?\s*(-?\d+(?:\.\d+)?)\s*$", str(token or ""))
    if not match:
        return None
    key = _clean_prompt_meta_key(match.group(1))
    if not key:
        return None
    raw_value = float(match.group(2))
    value: Any = int(raw_value) if key == "num_step" else raw_value
    return key, value


def _parse_prompt_entry_line(line: str) -> Optional[Dict[str, Any]]:
    cleaned = str(line or "").strip()
    if not cleaned or ":" not in cleaned:
        return None
    audio_file, payload = cleaned.split(":", 1)
    audio_file = audio_file.strip()
    payload = payload.strip()
    if not audio_file or not payload:
        return None

    parts = [part.strip() for part in payload.split(",")]
    if not parts:
        return None

    default_config: Dict[str, Any] = {}
    text_parts = list(parts)
    while text_parts:
        parsed = _parse_prompt_meta_token(text_parts[-1])
        if not parsed:
            break
        key, value = parsed
        default_config[key] = value
        text_parts.pop()

    text = ", ".join(part for part in text_parts if part).strip()
    if not text:
        return None

    for token in parts[len(text_parts):]:
        parsed = _parse_prompt_meta_token(token)
        if parsed:
            key, value = parsed
            default_config[key] = value

    return {
        "audio_file": audio_file,
        "reference_text": text,
        "default_config": default_config,
    }


@lru_cache(maxsize=16)
def _prompt_entries_for_lang(lang: str) -> Dict[str, Dict[str, Any]]:
    path = _prompt_text_path(lang)
    if not path or not path.exists():
        return {}

    entries: Dict[str, Dict[str, Any]] = {}
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return {}

    for line in raw_lines:
        entry = _parse_prompt_entry_line(line)
        if not entry:
            continue
        audio_file = str(entry["audio_file"])
        entries[audio_file.lower()] = entry
        entries[Path(audio_file).stem.lower()] = entry
    return entries


def _prompt_entry_for_audio(lang: str, audio_file: Optional[str]) -> Optional[Dict[str, Any]]:
    if not audio_file:
        return None
    entries = _prompt_entries_for_lang(lang)
    if not entries:
        return None
    filename = Path(str(audio_file)).name.lower()
    stem = Path(str(audio_file)).stem.lower()
    return entries.get(filename) or entries.get(stem)


def get_emotion_presets_for_lang(lang: str) -> Dict[str, Any]:
    lang = canonical_lang(lang)
    return EMOTION_PRESETS_BY_LANG.get(lang, EMOTION_PRESETS_BY_LANG.get("vi", {}))


def get_ad_presets_for_lang(lang: str) -> Dict[str, Any]:
    lang = canonical_lang(lang)
    return AD_PRESETS_BY_LANG.get(lang, {"Không bổ trợ": {"overrides": {}, "gain_db": 0.0}})


def _normalize_style_key(value: Optional[str]) -> str:
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return " ".join(text.lower().strip().split())


def _resolve_style_key(options: Dict[str, Any], key: Optional[str]) -> Optional[str]:
    if key in options:
        return str(key)
    normalized = _normalize_style_key(key)
    for candidate in options:
        if _normalize_style_key(candidate) == normalized:
            return candidate
    return None


def get_lang_config(
    lang: str,
    overrides: Optional[Dict[str, Any]] = None,
    base_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    lang = canonical_lang(lang)
    cfg = dict(BASE_OMNI_CONFIG)
    cfg.update(LANGUAGE_PRESETS.get(lang, {}))
    cfg.update(DEPLOY_LANGUAGE_CONFIGS.get(lang, {}))
    if base_overrides:
        cfg.update({key: value for key, value in base_overrides.items() if value is not None})
    if overrides:
        cfg.update({key: value for key, value in overrides.items() if value is not None})
    return cfg


def get_effective_config(
    lang: str,
    emotion_key: str,
    ad_key: str = "Không bổ trợ",
    manual_overrides: Optional[Dict[str, Any]] = None,
    base_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    lang = canonical_lang(lang)
    cfg = get_lang_config(lang, base_overrides=base_overrides)
    ad_options = get_ad_presets_for_lang(lang)
    emotion_options = get_emotion_presets_for_lang(lang)
    resolved_ad_key = _resolve_style_key(ad_options, ad_key)
    resolved_emotion_key = _resolve_style_key(emotion_options, emotion_key)
    ad_cfg = ad_options.get(resolved_ad_key or "", {"overrides": {}, "gain_db": 0.0})
    emotion_cfg = emotion_options.get(resolved_emotion_key or "", {"overrides": {}, "gain_db": 0.0})
    
    # Combine overrides: AD adjustments are baseline, Emotion adjustments are additive relative to 1.0
    # This prevents Emotion from completely wiping out AD intensity changes.
    ad_overrides = ad_cfg.get("overrides", {})
    emotion_overrides = emotion_cfg.get("overrides", {})

    cfg.update(ad_overrides)
    for key, val in emotion_overrides.items():
        if key in ["speed", "pitch_shift"] and key in cfg:
            # Additive relative to 1.0: final = current_adjusted + (emotion_val - 1.0)
            cfg[key] = float(cfg[key]) + (float(val) - 1.0)
        else:
            # Other parameters (silences, num_step) still use emotion-specific values
            cfg[key] = val

    if manual_overrides:
        cfg.update({key: value for key, value in manual_overrides.items() if value is not None})
    return cfg


def safe_gain_for_style(lang: str, emotion_key: str, ad_key: str, enabled: bool = True) -> float:
    if not enabled:
        return 0.0
    lang = canonical_lang(lang)
    emotion_options = get_emotion_presets_for_lang(lang)
    ad_options = get_ad_presets_for_lang(lang)
    emotion_cfg = emotion_options.get(_resolve_style_key(emotion_options, emotion_key) or "", {"gain_db": 0.0})
    ad_cfg = ad_options.get(_resolve_style_key(ad_options, ad_key) or "", {"gain_db": 0.0})
    return float(emotion_cfg.get("gain_db", 0.0)) + float(ad_cfg.get("gain_db", 0.0))


def apply_ad_safe_guard(lang: str, cfg: Dict[str, Any], enabled: bool = True) -> Dict[str, Any]:
    return dict(cfg)


def clamp_prosody(lang: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    lang = canonical_lang(lang)
    clamped = dict(cfg)
    if lang in {"en", "vi"}:
        return clamped
    elif lang == "my":
        speed_min, speed_max = 0.90, 2.0
        pitch_min, pitch_max = 0.96, 1.06
    else:
        speed_min, speed_max = 0.88, 1.15
        pitch_min, pitch_max = 0.94, 1.08
    clamped["speed"] = max(speed_min, min(speed_max, float(clamped.get("speed", 1.0))))
    clamped["pitch_shift"] = max(pitch_min, min(pitch_max, float(clamped.get("pitch_shift", 1.0))))
    return clamped


def stabilize_clone_config(lang: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    return dict(cfg)


def _build_voice_preset(defn: Dict[str, Any], lang: str) -> Dict[str, Any]:
    audio_path = None
    if PROMPT_VOICE_ROOT:
        audio_path = PROMPT_VOICE_ROOT / str(defn["folder"]) / str(defn["audio_file"])
    ref_text_path = _prompt_text_path(lang)
    prompt_entry = _prompt_entry_for_audio(lang, str(defn["audio_file"]))
    ref_text = str(prompt_entry.get("reference_text") or "") if prompt_entry else _prompt_text(lang)
    default_config = dict(prompt_entry.get("default_config", {})) if prompt_entry else {}
    return {
        "preset_key": str(defn["preset_key"]),
        "label": str(defn["label"]),
        "language": canonical_lang(lang),
        "audio_path": str(audio_path) if audio_path else "",
        "exists": bool(audio_path and audio_path.exists()),
        "reference_text_path": str(ref_text_path) if ref_text_path else "",
        "reference_text": ref_text,
        "reference_text_exists": bool(ref_text_path and ref_text_path.exists() and ref_text),
        "default_config": default_config,
        "style_tags": list(defn.get("style_tags", [])),
        "aliases": list(defn.get("aliases", [])),
    }


def list_voice_presets() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for lang, definitions in VOICE_DEFINITIONS.items():
        for definition in definitions:
            items.append(_build_voice_preset(definition, lang))
    return items


def get_voice_preset(preset_key: Optional[str]) -> Optional[Dict[str, Any]]:
    if not preset_key:
        return None
    normalized = str(preset_key).strip().lower()
    for preset in list_voice_presets():
        keys = [str(preset.get("preset_key", "")).lower()] + [str(alias).lower() for alias in preset.get("aliases", [])]
        if normalized in keys:
            return preset
    return None


def build_prompt_voice_healthcheck() -> Dict[str, Any]:
    presets = list_voice_presets()
    available = [item for item in presets if item.get("exists")]
    missing = [item for item in presets if not item.get("exists")]
    reference_text_paths = {
        lang: str(path) if path else None
        for lang, path in ((lang, _prompt_text_path(lang)) for lang in LANGUAGE_LABELS)
    }
    reference_text_exists = {
        lang: bool(path and Path(path).exists())
        for lang, path in reference_text_paths.items()
    }
    return {
        "prompt_voice_root": str(PROMPT_VOICE_ROOT) if PROMPT_VOICE_ROOT else None,
        "available_presets": available,
        "missing_presets": missing,
        "available_count": len(available),
        "missing_count": len(missing),
        "reference_text_paths": reference_text_paths,
        "reference_text_exists": reference_text_exists,
    }
