import json
import os, re
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document

def initialize_faiss_db(json_path, save_path):
    # 1. Khởi tạo Embedding Model 
    # Một số embedding models phổ biến cho tiếng Việt: "vinai/phobert-base", "vinai/phobert-large", "dangvantuan/vietnamese-embedding"
    model_name = "bkai-foundation-models/vietnamese-bi-encoder"
    embeddings = HuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs={'device': 'cpu'} 
    )

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    documents = []
    
    for filename, file_data in data.items():
        chunks = file_data.get("chunks", [])
        
        for chunk in chunks:
            content = chunk.get("content", "")
            meta = chunk.get("metadata", {})
            
            # --- KỸ THUẬT: CONTEXT ENRICHMENT (Dán tiêu đề vào nội dung) ---
            # Giúp Vector bám sát ngữ cảnh của Điều/Mục đó
            section_title = meta.get("section", "")
            subsection_title = meta.get("subsection", "")
            
            header_prefix = ""
            if section_title:
                header_prefix += f"{section_title}\n"
            if subsection_title:
                header_prefix += f"{subsection_title}\n"
            
            # Nội dung nạp vào FAISS sẽ bao gồm cả Tiêu đề + Nội dung chi tiết
            full_content = f"{header_prefix}{content}".strip()
            
            doc = Document(
                page_content=full_content,
                metadata=meta
            )
            documents.append(doc)

    # 3. Khởi tạo FAISS từ danh sách Documents
    if documents:
        print(f"Bắt đầu Embedding {len(documents)} chunks...")
        vector_db = FAISS.from_documents(documents, embeddings)
        
        # 4. Lưu trữ Local (Tương tự persist của ChromaDB)
        vector_db.save_local(save_path)
        print(f"✅ Đã lưu FAISS Index tại: {save_path}")
        return vector_db
    else:
        print("❌ Không tìm thấy dữ liệu để nạp!")
        return None

# --- Khởi tạo FAISS Index ---
JSON_INPUT = "uit_dataset_v2.json"
DB_PATH = "v2_uit_faiss_index"

# Khởi tạo DB
vector_db = initialize_faiss_db(JSON_INPUT, DB_PATH)

# Lưu trữ xuống thư mục local
vector_db.save_local(DB_PATH)
print(f"Đã lưu index vào thư mục: {DB_PATH}")