import pandas as pd
import numpy as np
import os

from sentence_transformers import CrossEncoder
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS

from rank_bm25 import BM25Okapi
from pyvi import ViTokenizer

import ollama

import warnings
warnings.filterwarnings("ignore")

# --- KHỞI TẠO MODEL & DATABASE ---
DB_PATH = "v2_uit_faiss_index"
print("⚙️ Đang khởi tạo Model và Database...")
EMBEDDINGS = HuggingFaceEmbeddings(model_name="bkai-foundation-models/vietnamese-bi-encoder")
RERANK_MODEL = CrossEncoder('itdainb/PhoRanker', max_length=256)
DB = FAISS.load_local(DB_PATH, EMBEDDINGS, allow_dangerous_deserialization=True)

print("🔍 Đang khởi tạo bộ chỉ mục từ khóa BM25 (có phân từ tiếng Việt)...")
all_docs = list(DB.docstore._dict.values()) 

# ViTokenizer
tokenized_corpus = []
for doc in all_docs:
    segmented_text = ViTokenizer.tokenize(doc.page_content.lower())
    tokenized_corpus.append(segmented_text.split())

bm25 = BM25Okapi(tokenized_corpus)


# --- HÀM TÌM KIẾM HYBRID ---
def query_uit_regulations(query_text, k=10):
    # 1. Tìm bằng Vector (FAISS)
    faiss_results = DB.similarity_search_with_score(query_text, k=k)
    
    # 2. Tìm bằng Từ khóa (BM25)
    segmented_query = ViTokenizer.tokenize(query_text.lower())
    
    # Lọc stop words
    stop_words = ["là", "của", "và", "các", "những", "cho", "trong", "về", "việc"]
    query_tokens = [word for word in segmented_query.split() if word not in stop_words]
    
    # Tránh trường hợp câu hỏi toàn stop words bị lọc sạch
    if not query_tokens:
        query_tokens = segmented_query.split()
        
    bm25_scores = bm25.get_scores(query_tokens)

    # Lấy top k kết quả từ BM25
    top_n_idx = np.argsort(bm25_scores)[-k:][::-1]
    bm25_results = [(all_docs[i], bm25_scores[i]) for i in top_n_idx]

    # 3. Gộp context từ cả hai nguồn
    combined_docs = {}
    
    # Thêm từ FAISS
    for doc, _ in faiss_results:
        combined_docs[doc.page_content] = doc
        
    # Thêm từ BM25
    for doc, _ in bm25_results:
        if doc.page_content not in combined_docs:
            combined_docs[doc.page_content] = doc
            
    # Chuyển về dạng list để đưa qua PhoRanker
    final_list = [[doc, 0] for doc in combined_docs.values()]
    return final_list

def rerank_documents(query, retrieved_docs):
    if not retrieved_docs:
        return []
    
    # Tạo các cặp (Câu hỏi, Tài liệu) để Cross-Encoder chấm điểm
    pairs = [[query, doc.page_content] for doc, score in retrieved_docs]
    
    cross_scores = RERANK_MODEL.predict(pairs)
    
    scored_docs = []
    for i in range(len(retrieved_docs)):
        doc = retrieved_docs[i][0]
        scored_docs.append((doc, cross_scores[i]))
    
    scored_docs.sort(key=lambda x: x[1], reverse=True)
    
    return scored_docs[:5]

def rewrite_query_with_history(user_query, chat_history):
    if not chat_history:
        return user_query
        
    # Tạo prompt để LLM hiểu ngữ cảnh cũ
    history_context = "\n".join([f"{m['role']}: {m['content']}" for m in chat_history[-3:]])
    prompt = f"Lịch sử chat:\n{history_context}\n\nCâu hỏi mới: {user_query}\n\nHãy viết lại câu hỏi mới này thành một câu hỏi đầy đủ, rõ ràng để tra cứu quy định. Trả lời DUY NHẤT câu hỏi đã viết lại."
    
    # Dùng Qwen để viết lại câu hỏi
    response = ollama.chat(model='qwen3:4b', messages=[{'role': 'user', 'content': prompt}])
    return response['message']['content']

def generate_uit_response(query, retrieved_docs):
    context_text = ""
    for i, (doc, score) in enumerate(retrieved_docs):
        tables = doc.metadata.get('tables', [])
        if tables:
            context_text += f"\n[Dữ liệu bảng đi kèm]: {str(tables)}\n"
        source = doc.metadata.get('section', 'Quy định chung')
        doc_no = doc.metadata.get('doc_number', 'Chưa rõ số hiệu')
        context_text += f"\n[Nguồn {i+1}: {source} - Số hiệu: {doc_no}]\n{doc.page_content}\n"

    system_prompt = (
    "Bạn là Trợ lý ảo tra cứu quy định của Đại học Công nghệ Thông tin (UIT). "
    "\n\nNHIỆM VỤ:"
    "\n- Nhắc lại nội dung câu hỏi và phần trả lời, trả lời trục tiếp, ngắn gọn, chính xác, dựa hoàn toàn vào Context đã cho."
    "\n- Giữ nguyên thuật ngữ chuyên môn từ văn bản gốc."
    )

    user_message = f"Dựa vào Context sau đây:\n{context_text}\n\nHãy trả lời câu hỏi: {query}"

    try:
        response = ollama.chat(
            model='qwen3:4b', 
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_message},
            ],
            options={'temperature': 0.1, 'num_ctx': 4096}
        )
        return response['message']['content']
    except Exception as e:
        return f"Lỗi kết nối Ollama: {str(e)}"
