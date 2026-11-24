import argparse
import json
import os
import shutil
from datetime import datetime, timezone

import torch
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_community.vectorstores.utils import DistanceStrategy

# ============================================================
# CONFIG
# ============================================================
MODEL_NAME = "bkai-foundation-models/vietnamese-bi-encoder"
JSON_INPUT = "uit_dataset.json"
DB_PATH = "uit_faiss_index"
PARENT_STORE_PATH = "parent_store.json"

DEFAULT_BATCH_SIZE = 32
NORMALIZE_EMBEDDINGS = True


def resolve_device(requested_device="auto"):
    if requested_device != "auto":
        return requested_device
    return "cuda" if torch.cuda.is_available() else "cpu"


def load_dataset(json_path):
    if not os.path.isfile(json_path):
        raise FileNotFoundError(f"Không tìm thấy dataset: {json_path}")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("Dataset phải là JSON object.")

    documents = data.get("documents")
    if not isinstance(documents, list):
        raise ValueError("Dataset không đúng schema v3: thiếu trường 'documents' dạng list.")

    return data


def compact_metadata(metadata):
    return {key: value for key, value in metadata.items() if value is not None and value != ""}


def build_child_documents(data):
    langchain_documents = []
    skipped_chunks = 0
    duplicate_chunk_ids = 0
    seen_chunk_ids = set()

    for document in data.get("documents", []):
        document_meta = document.get("metadata", {}) or {}
        document_doc_id = document.get("doc_id") or document_meta.get("doc_id")
        source_file = document.get("source_file") or document_meta.get("source_file")

        for chunk in document.get("chunks", []):
            retrieval_text = (chunk.get("retrieval_text") or "").strip()
            content = (chunk.get("content") or "").strip()
            chunk_meta = chunk.get("metadata", {}) or {}

            if not retrieval_text:
                skipped_chunks += 1
                continue

            chunk_id = chunk.get("chunk_id")
            if chunk_id and chunk_id in seen_chunk_ids:
                duplicate_chunk_ids += 1
                continue

            if chunk_id:
                seen_chunk_ids.add(chunk_id)

            metadata = {
                **document_meta,
                **chunk_meta,
                "doc_id": chunk_meta.get("doc_id") or document_doc_id,
                "source_file": chunk_meta.get("source_file") or source_file,
                "chunk_id": chunk_id,
                "parent_id": chunk.get("parent_id"),
                "chunk_type": chunk.get("chunk_type"),
                "sequence": chunk.get("sequence"),
                "content": content
            }

            langchain_documents.append(Document(page_content=retrieval_text, metadata=compact_metadata(metadata)))

    return langchain_documents, {
        "indexed_chunks": len(langchain_documents),
        "skipped_empty_chunks": skipped_chunks,
        "duplicate_chunk_ids": duplicate_chunk_ids
    }


def build_parent_store(data):
    parents = {}
    duplicate_parent_ids = 0
    skipped_parents = 0

    for document in data.get("documents", []):
        document_meta = document.get("metadata", {}) or {}
        document_doc_id = document.get("doc_id") or document_meta.get("doc_id")
        source_file = document.get("source_file") or document_meta.get("source_file")

        for parent in document.get("parents", []):
            parent_id = parent.get("parent_id")

            if not parent_id:
                skipped_parents += 1
                continue

            if parent_id in parents:
                duplicate_parent_ids += 1
                continue

            stored_parent = dict(parent)
            stored_parent["doc_id"] = stored_parent.get("doc_id") or document_doc_id
            stored_parent["source_file"] = stored_parent.get("source_file") or source_file
            stored_parent["document_metadata"] = document_meta
            parents[parent_id] = stored_parent

    return parents, {
        "stored_parents": len(parents),
        "skipped_parents": skipped_parents,
        "duplicate_parent_ids": duplicate_parent_ids
    }


def create_embeddings(model_name, device, batch_size):
    print(f"Embedding model : {model_name}")
    print(f"Device          : {device}")

    return HuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs={"device": device},
        encode_kwargs={
            "normalize_embeddings": NORMALIZE_EMBEDDINGS,
            "batch_size": batch_size
        },
        show_progress=True
    )


def save_parent_store(parent_store, output_path, source_dataset, model_name):
    payload = {
        "schema_version": "1.0",
        "source_dataset": os.path.basename(source_dataset),
        "embedding_model": model_name,
        "parents": parent_store
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def save_manifest(db_path, source_dataset, parent_store_path, model_name, device, batch_size, dataset_schema_version, child_stats, parent_stats):
    manifest = {
        "index_version": "1.0",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_dataset": os.path.abspath(source_dataset),
        "dataset_schema_version": dataset_schema_version,
        "embedding_model": model_name,
        "normalize_embeddings": NORMALIZE_EMBEDDINGS,
        "distance_strategy": "MAX_INNER_PRODUCT",
        "similarity_interpretation": "Cosine-equivalent because embeddings are L2-normalized before inner-product search.",
        "device_used_for_build": device,
        "batch_size": batch_size,
        "parent_store": os.path.abspath(parent_store_path),
        "child_stats": child_stats,
        "parent_stats": parent_stats
    }

    manifest_path = os.path.join(db_path, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    return manifest_path


def prepare_output(db_path, parent_store_path, overwrite=False):
    if os.path.exists(db_path):
        if not overwrite:
            raise FileExistsError(f"FAISS index đã tồn tại tại '{db_path}'. Chạy lại với --overwrite nếu muốn tạo lại.")
        shutil.rmtree(db_path)

    if os.path.exists(parent_store_path):
        if not overwrite:
            raise FileExistsError(f"Parent store đã tồn tại tại '{parent_store_path}'. Chạy lại với --overwrite nếu muốn tạo lại.")
        os.remove(parent_store_path)


def initialize_faiss_db(json_path, save_path, parent_store_path, model_name=MODEL_NAME, device="auto", batch_size=DEFAULT_BATCH_SIZE, overwrite=False):
    print("📚 Đang đọc dataset...")
    data = load_dataset(json_path)
    dataset_schema_version = data.get("schema_version", "unknown")

    print(f"Dataset schema   : {dataset_schema_version}")
    print(f"Số documents    : {len(data.get('documents', []))}")

    print("\n🔎 Đang chuẩn bị child chunks để index...")
    child_documents, child_stats = build_child_documents(data)

    if not child_documents:
        raise ValueError("Không có child chunk hợp lệ để tạo FAISS index.")

    print(f"Child hợp lệ     : {child_stats['indexed_chunks']}")
    print(f"Child rỗng      : {child_stats['skipped_empty_chunks']}")
    print(f"Chunk ID trùng  : {child_stats['duplicate_chunk_ids']}")

    print("\nĐang tạo Parent Store...")
    parent_store, parent_stats = build_parent_store(data)
    print(f"Parents          : {parent_stats['stored_parents']}")
    print(f"Parent bỏ qua   : {parent_stats['skipped_parents']}")
    print(f"Parent ID trùng : {parent_stats['duplicate_parent_ids']}")

    prepare_output(save_path, parent_store_path, overwrite=overwrite)

    resolved_device = resolve_device(device)
    embeddings = create_embeddings(model_name, resolved_device, batch_size)

    print("\n🚀 Bắt đầu encode retrieval_text và tạo FAISS index...")
    vector_db = FAISS.from_documents(documents=child_documents, embedding=embeddings, distance_strategy=DistanceStrategy.MAX_INNER_PRODUCT)

    vector_db.save_local(save_path)
    print(f"✅ Đã lưu FAISS index tại: {save_path}")

    save_parent_store(parent_store, parent_store_path, json_path, model_name)
    print(f"✅ Đã lưu Parent Store tại: {parent_store_path}")

    manifest_path = save_manifest(save_path, json_path, parent_store_path, model_name, resolved_device, batch_size, dataset_schema_version, child_stats, parent_stats)
    print(f"✅ Đã lưu manifest tại: {manifest_path}")

    return vector_db


def parse_args():
    parser = argparse.ArgumentParser(description="Tạo FAISS child index + parent store cho UIT Regulation RAG schema v3.")
    parser.add_argument("--input", default=JSON_INPUT, help=f"Dataset JSON đầu vào. Mặc định: {JSON_INPUT}")
    parser.add_argument("--db-path", default=DB_PATH, help=f"Thư mục lưu FAISS. Mặc định: {DB_PATH}")
    parser.add_argument("--parent-store", default=PARENT_STORE_PATH, help=f"File JSON lưu parent chunks. Mặc định: {PARENT_STORE_PATH}")
    parser.add_argument("--model-name", default=MODEL_NAME, help=f"Embedding model. Mặc định: {MODEL_NAME}")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"], help="Thiết bị encode. 'auto' ưu tiên CUDA nếu có.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help=f"Batch size encode. Mặc định: {DEFAULT_BATCH_SIZE}")
    parser.add_argument("--overwrite", action="store_true", help="Cho phép ghi đè index/parent store đã tồn tại.")
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 72)
    print("UIT REGULATION RAG - CREATE FAISS DATABASE V3")
    print("=" * 72)

    initialize_faiss_db(json_path=args.input, save_path=args.db_path, parent_store_path=args.parent_store, model_name=args.model_name, device=args.device, batch_size=args.batch_size, overwrite=args.overwrite)

    print("\n🎉 Hoàn tất tạo database.")


if __name__ == "__main__":
    main()