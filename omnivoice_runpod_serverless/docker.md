Để **build/push nhanh hơn** cho case này, bạn nên kéo cache từ image đã có trên Docker Hub trước, rồi build với `--cache-from`.

Chạy theo thứ tự này:

```bash
cd "/Users/macbook/Desktop/OmniVoice Production/omnivoice_runpod_serverless"

docker pull nguyendangtri070304/omnivoice-runpod-worker:v20260519-myanmar-pause-v2

docker build \
  -f serverless/Dockerfile \
  --cache-from nguyendangtri070304/omnivoice-runpod-worker:v20260519-myanmar-pause-v2 \
  -t nguyendangtri070304/omnivoice-runpod-worker:v20260519-myanmar-pause-v2 \
  .

docker push nguyendangtri070304/omnivoice-runpod-worker:v20260519-myanmar-pause-v2
```

Nếu bạn muốn ra `v3` thay vì đè `v2`:

```bash
docker pull nguyendangtri070304/omnivoice-runpod-worker:v20260519-myanmar-pause-v2

docker build \
  -f serverless/Dockerfile \
  --cache-from nguyendangtri070304/omnivoice-runpod-worker:v20260519-myanmar-pause-v2 \
  -t nguyendangtri070304/omnivoice-runpod-worker:v20260519-myanmar-pause-v3 \
  .

docker push nguyendangtri070304/omnivoice-runpod-worker:v20260519-myanmar-pause-v3
```

Nếu vẫn muốn nhanh hơn nữa về lâu dài, workflow tốt hơn là dùng `buildx` với registry cache:

```bash
docker buildx build \
  -f serverless/Dockerfile \
  --cache-from type=registry,ref=nguyendangtri070304/omnivoice-runpod-worker:buildcache \
  --cache-to type=registry,ref=nguyendangtri070304/omnivoice-runpod-worker:buildcache,mode=max \
  -t nguyendangtri070304/omnivoice-runpod-worker:v20260519-myanmar-pause-v3 \
  --push \
  .
```

Cho repo này, cách ngắn gọn và thực dụng nhất là bắt đầu bằng `docker pull ...v2` rồi `docker build --cache-from ...v2 ...`. Điều đó thường đủ để lần sửa chỉ ở `app/presets.py` không phải build/push lại quá nhiều layer.