# Hướng dẫn Triển khai OmniVoice Web UI

Tài liệu này hướng dẫn cách triển khai hệ thống OmniVoice Web (Frontend + Backend + Database) sử dụng Docker.

## 1. Yêu cầu hệ thống
*   **Docker** & **Docker Compose** đã được cài đặt.
*   **RunPod API Key** và **RunPod Serverless Endpoint ID** (Phần serverless phải được deploy trước).

## 2. Cấu trúc mã nguồn quan trọng
*   `/web`: Chứa mã nguồn FastAPI Backend và Giao diện người dùng.
*   `/app`: Chứa nhân xử lý logic và cấu hình giọng đọc (Shared logic).
*   `docker-compose.yml`: File điều phối toàn bộ dịch vụ.
*   `init-db.sh/sql`: Tự động khởi tạo cấu trúc Database lần đầu.

## 3. Các bước triển khai

### Bước 1: Chuẩn bị biến môi trường
1. Sao chép file mẫu: `cp .env.example .env`
2. Mở file `.env` và điền các thông số quan trọng:
   *   `RUNPOD_API_KEY`: Key lấy từ dashboard RunPod.
   *   `RUNPOD_ENDPOINT_ID`: ID của Serverless Endpoint đã tạo.
   *   `POSTGRES_PASSWORD`: Mật khẩu cho cơ sở dữ liệu.
   *   `JWT_SECRET`: Một chuỗi ngẫu nhiên để bảo mật token đăng nhập.

### Bước 2: Khởi chạy hệ thống
Chạy lệnh sau tại thư mục gốc của dự án:
```bash
docker compose up -d --build
```

Hệ thống sẽ khởi chạy 3 dịch vụ:
1.  **omnivoice-web** (Cổng 7860): Giao diện chính.
2.  **omnivoice-db** (Cổng 5432): Database PostgreSQL.
3.  **omnivoice-adminer** (Cổng 8081): Công cụ quản trị DB trực quan.

## 4. Quản trị và Phê duyệt người dùng
Hệ thống sử dụng cơ chế **Approval-First**. Khi người dùng mới đăng ký, họ sẽ không thể đăng nhập cho đến khi Admin phê duyệt.

### Cách phê duyệt tài khoản:
1.  Truy cập Adminer: `http://<server-ip>:8081` (Đăng nhập với thông tin trong `.env`).
2.  Mở bảng `users`.
3.  Tìm tài khoản mới (cột `is_approved` đang là `false`).
4.  Chỉnh sửa (Edit) và chuyển `is_approved` thành `true` (hoặc tick vào ô tương ứng).
5.  Nhấn **Save**.

## 5. Kiểm tra trạng thái
*   **Logs Web:** `docker compose logs -f web`
*   **Stats API:** Truy cập `http://<server-ip>:7860/api/billing/stats` để xem thống kê sử dụng (Yêu cầu token admin).

---
**Lưu ý:** Tuyệt đối không push file `.env` lên GitLab/GitHub để đảm bảo an toàn cho tài khoản RunPod của bạn.


cd "omnivoice_runpod_serverless"

# 1. Pull image cũ về làm cache (giúp build nhanh hơn)
docker pull nguyendangtri070304/omnivoice-runpod-worker:<tag_cũ>

# 2. Build image mới
docker build -f serverless/Dockerfile \
  --cache-from nguyendangtri070304/omnivoice-runpod-worker:<tag_cũ> \
  -t nguyendangtri070304/omnivoice-runpod-worker:<tag_mới> .

# 3. Push image mới lên Docker Hub
docker push nguyendangtri070304/omnivoice-runpod-worker:<tag_mới>

docker compose up -d --build web


docker push nguyendangtri070304/omnivoice-runpod-worker:my5
