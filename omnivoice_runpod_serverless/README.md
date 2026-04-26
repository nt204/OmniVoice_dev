# OmniVoice Runpod Serverless package

Bản này đã được chỉnh để dùng **preset voice library** cho tiếng Việt, tiếng Khmer và tiếng Myanmar theo đúng cấu trúc file bạn yêu cầu.

## Test local trên CPU trước khi deploy

Code hiện hỗ trợ ép device bằng biến môi trường `OMNIVOICE_DEVICE`.

Nếu anh đang test trên Mac có MPS hoặc máy có GPU nhưng muốn ép chạy đúng CPU, dùng:

```bash
cd omnivoice_runpod_serverless
export OMNIVOICE_DEVICE=cpu
```

### 1. Cài dependency local

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Nếu model đã được tải sẵn ở local thì set thêm:

```bash
export MODEL_LOCAL_PATH=/duong-dan-toi-folder-model
```

Hoặc để code tự tìm model trong Hugging Face cache local:

```bash
export HF_HOME=/duong-dan-cache-huggingface
```

### 2. Chạy smoke test không cần load model

```bash
python3 -m unittest discover -s tests -t . -v
```

Các test này kiểm tra normalization, segmentation, preset voice registry, audio preprocessing và FastAPI UI mock path. Chúng không xác nhận inference model thật.

### 3. Chạy local handler trên CPU với payload có sẵn

Design mode, không cần audio ref:

```bash
python3 handler.py --test_file test_input_design.json
```

Clone mode:

```bash
python3 handler.py --test_file test_input_clone.json
```

Nếu muốn thấy rõ worker có đang chạy CPU hay không, bật `debug=true` trong payload. Response sẽ có trường:

```json
{
  "device": "cpu"
}
```

### 4. Chạy FastAPI local để test giao diện

```bash
uvicorn fastapi_app:app --host 0.0.0.0 --port 8000
```

Sau đó mở `http://127.0.0.1:8000` và kiểm tra:
- load trang UI
- preset voice library
- synthesize ở design mode
- synthesize ở clone mode nếu đã có audio ref hoặc preset thật

### 5. Checklist trước khi deploy Runpod serverless

- local response có `"ok": true`
- trường `"device"` đúng là `"cpu"` khi test CPU
- design mode chạy ổn với `test_input_design.json`
- clone mode chạy ổn với preset hoặc reference audio thật
- output wav được lưu đúng trong `OUTPUT_DIR`
- nếu dùng preset library, `list_voice_presets=true` trả về đúng file available/missing

## Những gì đã thêm
- hỗ trợ `voice_preset` để gọi thẳng giọng clone có sẵn
- tự lấy `reference_audio_path` từ thư viện giọng nếu không truyền path tay
- tự lấy `ref_text` từ `vietnam_prompt.txt`, `khmer_prompt.txt` hoặc `myanmar_prompt.txt` nếu không truyền tay
- giữ nguyên pipeline tiền xử lý audio clone: trim, VAD-like, denoise nhẹ, RMS normalize, peak normalize
- có endpoint kiểm tra thư viện preset bằng `list_voice_presets=true`
- có `voice_library` trả về trong response để bạn kiểm tra file nào đang có / thiếu trên volume
- mặc định production ưu tiên lưu file output, không trả `audio_base64` nếu bạn không bật thủ công
- metadata nặng như `voice_library`, `model_source`, traceback chỉ trả khi bật `debug=true`

---

## Cấu trúc thư mục bắt buộc trên Runpod volume
Mặc định worker sẽ đọc từ:

```text
/runpod-volume/prompt_voices/
├── vietnam_prompt_voice/
│   ├── giong_nam_ke_chuyen.mp3
│   ├── giong_nam_qc.mp3
│   ├── giong_nam_truong_thanh.mp3
│   ├── giong_nu_ke_chuyen.mp3
│   ├── giong_nu_qc.mp3
│   ├── giong_tre_em_qc.wav
│   └── vietnam_prompt.txt
├── khmer_prompt_voice/
│   ├── 1_khmer_audio_prompt.wav
│   ├── 2_khmer_audio_prompt.wav
│   ├── 3_khmer_audio_prompt.wav
│   ├── 4_khmer_audio_prompt.wav
│   └── khmer_prompt.txt
└── myanmar_prompt_voice/
    ├── 1_myanmar_audio_prompt.wav
    ├── 2_myanmar_audio_prompt.wav
    ├── 3_myanmar_audio_prompt.wav
    ├── 4_myanmar_audio_prompt.wav
    └── myanmar_prompt.txt
```

Nếu muốn dùng root khác, set biến môi trường:

```bash
PROMPT_VOICE_ROOT=/runpod-volume/prompt_voices
```

---

## Key preset có thể gọi trong request

### Tiếng Việt
- `vi_nam_ke_chuyen`
- `vi_nam_qc`
- `vi_nam_truong_thanh`
- `vi_nu_ke_chuyen`
- `vi_nu_qc`
- `vi_tre_em_qc`

### Tiếng Khmer
- `km_1`
- `km_2`
- `km_3`
- `km_4`

### Tiếng Myanmar
- `my_1`
- `my_2`
- `my_3`
- `my_4`

Ngoài ra có alias gần giống tên file như:
- `giong_nam_qc`
- `giong_nu_qc`
- `giong_tre_em_qc`
- `1_khmer_audio_prompt`
- `3_khmer_audio_prompt`
- `1_myanmar_audio_prompt`
- `3_myanmar_audio_prompt`

---

## Logic dùng preset
Nếu request có:

```json
{
  "voice_preset": "vi_nam_qc"
}
```

thì worker sẽ:
- tìm audio tại `/runpod-volume/prompt_voices/vietnam_prompt_voice/giong_nam_qc.mp3`
- tự đọc `vietnam_prompt.txt` làm `ref_text` nếu bạn không truyền `ref_text`
- tự áp preset prosody phù hợp hơn cho loại giọng đó
- vẫn cho phép bạn override lại `emotion`, `ad_emphasis`, `speed`, `pitch_shift`, `num_step`, `guidance_scale`

Ưu tiên như sau:
1. nếu bạn truyền `reference_audio_path` tay thì path tay được ưu tiên
2. nếu không truyền path tay nhưng có `voice_preset` thì lấy theo preset
3. nếu không truyền `ref_text` tay thì lấy từ file `*_prompt.txt`
4. nếu bạn không override prosody thì preset sẽ áp mặc định hợp lý hơn cho từng giọng

---

## Preset mặc định đã map

### Việt
- `vi_nam_ke_chuyen`: thiên về kể chuyện, ấm, nhẹ
- `vi_nam_qc`: thiên về quảng cáo, sáng, dứt khoát
- `vi_nam_truong_thanh`: nam trưởng thành, ổn định
- `vi_nu_ke_chuyen`: nữ kể chuyện, mềm
- `vi_nu_qc`: nữ quảng cáo, sáng, rõ
- `vi_tre_em_qc`: trẻ em quảng cáo, vui và lanh hơn

### Myanmar
- `my_1`: trung tính
- `my_2`: dịu hơn
- `my_3`: hợp nội dung quảng cáo / năng lượng hơn
- `my_4`: ổn định, cân bằng

### Khmer
- `km_1`: trung tính, dễ dùng cho nội dung phổ thông
- `km_2`: dịu hơn, hợp đọc mềm và tự nhiên
- `km_3`: hợp nội dung quảng cáo / năng lượng hơn
- `km_4`: ổn định, cân bằng, hợp nội dung nghiêm túc hơn

Đây là preset suy luận để tối ưu dùng thực tế. Chất giọng thật cuối cùng vẫn phụ thuộc vào audio ref gốc của bạn.

---

## Request mẫu: clone bằng preset Việt

```json
{
  "input": {
    "text": "Sản phẩm mới đã có mặt hôm nay. Mua ngay để nhận ưu đãi đặc biệt.",
    "language": "vi",
    "mode": "clone",
    "voice_preset": "vi_nam_qc",
    "emotion": "Hào hứng (Excited)",
    "ad_emphasis": "Cường điệu nhẹ",
    "preprocess_reference": true,
    "ref_trim_silence": true,
    "ref_apply_vad": true,
    "ref_apply_denoise": true,
    "ref_apply_rms_normalize": true,
    "ref_trim_top_db": 35,
    "ref_vad_top_db": 32,
    "ref_max_internal_silence_ms": 120,
    "ref_denoise_strength": 0.18,
    "ref_target_rms_dbfs": -22.0,
    "ref_min_seconds": 1.5,
    "ref_max_seconds": 10.0,
    "return_base64": false,
    "save_output": true,
    "debug": false
  }
}
```

## Request mẫu: clone bằng preset Myanmar

```json
{
  "input": {
    "text": "ဒီနေ့ပဲ သင်ကြိုက်တဲ့ ပရိုမိုးရှင်းကို ခံစားလိုက်ပါ။",
    "language": "my",
    "mode": "clone",
    "voice_preset": "my_3",
    "emotion": "Hào hứng (Excited)",
    "ad_emphasis": "Cường điệu nhẹ",
    "preprocess_reference": true,
    "ref_apply_vad": true,
    "ref_apply_denoise": true,
    "ref_apply_rms_normalize": true,
    "return_base64": false,
    "save_output": true,
    "debug": false
  }
}
```

## Request mẫu: clone bằng preset Khmer

```json
{
  "input": {
    "text": "សាកល្បងសំឡេងខ្មែរ សម្រាប់ការផ្សព្វផ្សាយ និងការនិយាយធម្មជាតិ។",
    "language": "km",
    "mode": "clone",
    "voice_preset": "km_3",
    "emotion": "Hào hứng (Excited)",
    "ad_emphasis": "Cường điệu nhẹ",
    "preprocess_reference": true,
    "ref_apply_vad": true,
    "ref_apply_denoise": true,
    "ref_apply_rms_normalize": true,
    "return_base64": false,
    "save_output": true,
    "debug": false
  }
}
```

---

## Request mẫu: kiểm tra thư viện giọng trên server

```json
{
  "input": {
    "text": "dummy",
    "list_voice_presets": true
  }
}
```

Response sẽ trả về:
- `prompt_voice_root`
- `available_presets`
- `missing_presets`
- `available_count`
- `missing_count`
- path txt tham chiếu của từng ngôn ngữ

---

## Các đường dẫn quan trọng
- `/app/handler.py`: entrypoint worker
- `/app/app/engine.py`: luồng xử lý chính
- `/app/app/audio_processing.py`: pipeline tiền xử lý audio clone
- `/app/app/presets.py`: registry preset giọng và path mapping
- `/app/fastapi_app.py`: giao diện production FastAPI dùng trực tiếp backend app
- `/runpod-volume/prompt_voices`: thư viện giọng clone
- `/runpod-volume/outputs`: nơi lưu file wav đầu ra
- `/runpod-volume/prompt-cache`: cache voice clone prompt
- `/runpod-volume/huggingface-cache`: cache model HF
- `/runpod-volume/torch-cache`: cache torch

---

## Chạy giao diện production cục bộ

Nếu muốn giao diện gần giống notebook nhưng dùng thẳng backend production:

```bash
cd omnivoice_runpod_serverless
python3 fastapi_app.py
```

UI sẽ mở trên port `7860`. Có 2 mode chạy:
- nếu có `RUNPOD_ENDPOINT_ID` và `RUNPOD_API_KEY`, web sẽ gọi Runpod Serverless từ xa và không cần local AI runtime
- nếu không set 2 biến này, app sẽ fallback sang `OmniVoiceService.synthesize()` local
- `/tmp/omnivoice_<job_id>`: thư mục tạm mỗi request

---

## Biến môi trường nên set
```bash
MODEL_ID=k2-fsa/OmniVoice
MODEL_LOCAL_PATH=/runpod-volume/models/OmniVoice
OUTPUT_DIR=/runpod-volume/outputs
PROMPT_CACHE_DIR=/runpod-volume/prompt-cache
PROMPT_VOICE_ROOT=/runpod-volume/prompt_voices
HF_HOME=/runpod-volume/huggingface-cache
TRANSFORMERS_CACHE=/runpod-volume/huggingface-cache
TORCH_HOME=/runpod-volume/torch-cache
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
```

---

## Gợi ý tối ưu chất lượng clone
- clip ref tốt nhất dài khoảng 3–10 giây
- chỉ 1 speaker
- không có nhạc nền
- không bị vang phòng nặng
- text trong `vietnam_prompt.txt`, `khmer_prompt.txt` và `myanmar_prompt.txt` nên khớp sát câu nói thật trong audio ref
- nếu chạy quảng cáo ngắn, ưu tiên `vi_nam_qc`, `vi_nu_qc`, `vi_tre_em_qc`, `my_3`
- nếu chạy kể chuyện, ưu tiên `vi_nam_ke_chuyen`, `vi_nu_ke_chuyen`, `my_2`
- với Khmer, có thể bắt đầu từ `km_3` cho nội dung quảng cáo và `km_2` hoặc `km_4` cho nội dung tự nhiên / cân bằng

---

## Local test
```bash
python -m unittest tests.test_smoke
python handler.py --test_input '{"input":{"text":"Xin chào","list_voice_presets":true}}'
```

## Quy trình test trước khi deploy production

### 1. Test logic local nhanh
Chạy ngay trong thư mục `omnivoice_runpod_serverless`:

```bash
python3 -m py_compile handler.py app/engine.py app/audio_processing.py
python3 handler.py --test_file test_input_list_presets.json
python3 handler.py --test_file test_input_design.json
```

Ý nghĩa:
- `py_compile`: bắt lỗi syntax trước khi build image
- `test_input_list_presets.json`: xác nhận worker đọc đúng thư viện preset trên volume/path
- `test_input_design.json`: xác nhận model load được và pipeline synthesize chạy được không cần reference audio

### 2. Test clone mode thật
Sau khi đã mount đúng prompt voice library hoặc đặt đúng path local tương đương:

```bash
python3 handler.py --test_file test_input_clone.json
```

Kiểm tra trong response:
- `ok` phải là `true`
- `output_path` phải có giá trị
- `reference.voice_preset` phải đúng preset bạn gọi
- `reference.quality_after.warnings` càng ít càng tốt

### 3. Nếu muốn debug sâu
Tạm thêm `debug: true` vào file test input để xem thêm:
- `voice_library`
- `model_source`
- `traceback` khi có lỗi

### 4. Test trong container trước khi đẩy Runpod
Build image:

```bash
export VERSION=v1.0.2
docker build --platform linux/amd64 -f serverless/Dockerfile -t nguyendangtri070304/omnivoice-runpod-worker:${VERSION} .
```

Run local container:

```bash
docker run --rm -it \
  -v /path/to/your/runpod-volume:/runpod-volume \
  nguyendangtri070304/omnivoice-runpod-worker:${VERSION} \
  python -u handler.py --test_file /app/test_input_design.json
```

Nếu test clone:

```bash
docker run --rm -it \
  -v /path/to/your/runpod-volume:/runpod-volume \
  nguyendangtri070304/omnivoice-runpod-worker:${VERSION} \
  python -u handler.py --test_file /app/test_input_clone.json
```

### 5. Test staging trên Runpod trước production
Tạo một endpoint staging riêng và test theo thứ tự:
- `list_voice_presets=true`
- `design`
- `clone` với `voice_preset`
- `clone` với `reference_audio_url` hoặc `reference_audio_base64` nếu production sẽ dùng kiểu input đó

Chỉ đẩy production sau khi cả 4 case đều pass.

---

## Build image
```bash
export VERSION=v1.0.2
docker build --platform linux/amd64 -f serverless/Dockerfile -t nguyendangtri070304/omnivoice-runpod-worker:${VERSION} .
docker build --platform linux/amd64 -f web/Dockerfile -t nguyendangtri070304/omnivoice-runpod-web:${VERSION} .
```

## Tách image cho web và worker

`Dockerfile.worker`:
- dành cho Runpod Serverless worker
- có `torch`, `torchaudio`, `omnivoice`
- image nặng nhưng cần cho inference

`Dockerfile.web`:
- dành cho CPU pod chạy FastAPI UI
- chỉ cài `fastapi`, `uvicorn`, `requests`, `python-multipart`
- không kéo `torch` hay `omnivoice`, nên pull/start nhanh hơn nhiều

Ví dụ chạy web image:

```bash
docker run --rm -it -p 7860:7860 \
  -e RUNPOD_ENDPOINT_ID=your_endpoint_id \
  -e RUNPOD_API_KEY=your_api_key \
  -e PROMPT_VOICE_ROOT=/runpod-volume/prompt_voices \
  -v /path/to/your/runpod-volume:/runpod-volume \
  nguyendangtri070304/omnivoice-runpod-web:${VERSION}
```

Web image sẽ chỉ serve UI, preset preview và forward request sang endpoint serverless.

---

## Ghi chú trung thực

- mặc định API hiện tại:
  - `return_base64=false`
  - `save_output=true`
  - `debug=false`
- nếu bạn cần base64 để gọi từ client không dùng volume/file output, hãy bật `return_base64=true`
- nếu cần metadata chẩn đoán khi audit production, bật `debug=true`
Bản này đã được chỉnh để path và preset logic chạy đúng ở mức code/package, và đã có test smoke cho registry + preprocessing. Việc xác nhận chất lượng âm thanh cuối cùng vẫn cần chạy thật trên GPU Runpod với đúng weights OmniVoice và đúng audio mẫu của bạn.
