# 🎓 UIT Chatbot - Trợ lý ảo tra cứu quy định học vụ

UIT Chatbot là một hệ thống RAG (Retrieval-Augmented Generation) được thiết kế để hỗ trợ sinh viên tra cứu nhanh chóng và chính xác các quy định, quy chế đào tạo tại Trường Đại học Công nghệ Thông tin - ĐHQG HCM.

## 📝 Tổng quan dự án

* **Dữ liệu:** Toàn bộ quy định học vụ, quy chế thi, đào tạo của UIT (định dạng .pdf, .docx).
* **Mục tiêu:** Cung cấp câu trả lời chính xác, có trích dẫn nguồn (Điều/Khoản) hỗ trợ quá trình tìm kiếm thông tin nhanh chóng tiện lợi.

### 🛠 Tech Stack sử dụng

* **Frontend:** React, TypeScript, Vite, Lucide React, Tailwind CSS.
* **Backend:** FastAPI (Python).
* **LLM & Embedding:** * **LLM:** Qwen 4B (chạy qua Ollama).
    * **Embedding:** `bkai-foundation-models/vietnamese-bi-encoder`.
    * **Reranker:** `itdainb/PhoRanker`.
* **Vector Database:** FAISS.
* **Search Engine:** Hybrid Search (FAISS + BM25 với `pyvi` phân từ).

---

## 📊 Chi tiết về dữ liệu & Pipeline xử lý

### 1. Dữ liệu (Data)
Dữ liệu được trích xuất từ các văn bản hành chính của trường. Để đảm bảo hiệu quả tìm kiếm, dữ liệu được xử lý qua các bước:
* **Loại bỏ nhiễu:** Xóa bỏ quốc hiệu, tiêu ngữ, thông tin nơi nhận và các thành phần hành chính không mang nội dung quy định.
* **Chunking:** Sử dụng chiến thuật **Logical Chunking** (chia theo từng Điều/Khoản) kết hợp với **Recursive Character Splitting** (kích thước ~600 ký tự).
* **Metadata:** Gắn nhãn Chương, Điều, Tên văn bản và Ngày ban hành vào từng đoạn.

### 2. Pipeline xử lý dữ liệu (Data Pipeline)
1.  **Extract:** Đọc file `.docx`, `.pdf` (giữ nguyên cấu trúc bảng biểu).
2.  **Clean:** Làm sạch văn bản bằng Regex.
3.  **Enrich:** Ép Metadata (Chương/Điều) trực tiếp vào nội dung text để tăng độ nhạy cho Retrieval.
4.  **Vectorize:** Chuyển đổi sang Vector và lưu trữ vào FAISS index.

---

## 🔍 Pipeline truy vấn (Inference Pipeline)

Hệ thống sử dụng quy trình **Retrieve & Re-rank** hai giai đoạn:

1.  **Query Rewriting:** Sử dụng LLM để viết lại câu hỏi của người dùng dựa trên 3 lượt hội thoại gần nhất nhằm hiểu rõ ngữ cảnh.
2.  **Hybrid Search:**
    * **Vector Search (FAISS):** Tìm kiếm theo ngữ nghĩa sâu sắc.
    * **Keyword Search (BM25):** Sử dụng `pyvi` để tách từ tiếng Việt, giúp tìm chính xác các từ khóa đặc thù (mã môn, học phí...).
3.  **Reranking:** Sử dụng `PhoRanker` để chấm điểm lại Top 15-20 tài liệu tìm được, lấy ra Top 5 đoạn văn bản chất lượng nhất.
4.  **Generation:** Đưa Context và câu hỏi vào Prompt được thiết kế theo kỹ thuật *Zero-hallucination* để tạo câu trả lời.

---

## 📂 Cấu trúc thư mục

```text
Chatbot_UIT/
├── uit_faiss_index/            # Cơ sở dữ liệu Vector (cần file data.json để build)
├── data_preprocessing
    ├── processing_raw_data.py  # Script tiền xử lý và chunking dữ liệu
    └── create_database.py      # Tạo DB
├── ai_core.py                  # Logic RAG: Hybrid Search, Rerank, Ollama
├── main.py                     # API Backend (FastAPI)
└── chatbot-uit/                # Source code Frontend (React + Vite)
    ├── src/
    │   ├── App.tsx             # Giao diện chính và quản lý History
    │   └── main.tsx
    └── package.json
```
## 🚀 Hướng dẫn chạy

### 1. Cài đặt Backend
```bash
# Cài đặt các thư viện cần thiết
pip install -r requirements.txt

# Khởi động Ollama và tải model (Đảm bảo đã cài đặt Ollama trước đó)
ollama run qwen3:4b

# Chạy server API
python main.py
```

### 2. Cài đặt Frontend
```bash
# Di chuyển vào thư mục giao diện
cd chatbot-uit-ui

# Cài đặt các gói phụ thuộc
npm install

# Khởi chạy ứng dụng ở chế độ phát triển
npm run dev
```



