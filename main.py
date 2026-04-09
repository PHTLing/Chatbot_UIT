from ast import List
from typing import List, Optional
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from ai_core import query_uit_regulations, rerank_documents, generate_uit_response, rewrite_query_with_history

app = FastAPI()

# Cho phép React (Vite) gọi API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"], # Port mặc định của Vite
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    query: str
    history: Optional[List[dict]] = [] 

@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    history = request.history if request.history else []
    rewritten_query = rewrite_query_with_history(request.query, history)
    raw_docs = query_uit_regulations(rewritten_query)
    refined_docs = rerank_documents(rewritten_query, raw_docs)
    answer = generate_uit_response(rewritten_query, refined_docs)
    return {"reply": answer}