FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/runpod-volume/huggingface-cache \
    TRANSFORMERS_CACHE=/runpod-volume/huggingface-cache \
    TORCH_HOME=/runpod-volume/torch-cache \
    OUTPUT_DIR=/runpod-volume/outputs \
    PROMPT_CACHE_DIR=/runpod-volume/prompt-cache \
    PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    torch==2.5.1 \
    torchaudio==2.5.1 \
    --index-url https://download.pytorch.org/whl/cu124

COPY serverless/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY serverless/handler.py /app/handler.py
COPY serverless/test_input_clone.json /app/test_input_clone.json
COPY serverless/test_input_design.json /app/test_input_design.json
COPY serverless/test_input_list_presets.json /app/test_input_list_presets.json

CMD ["python", "-u", "/app/handler.py"]
