import docx
import json
import os
import re
import pdfplumber
from docx.document import Document
from docx.oxml.text.paragraph import CT_P
from docx.oxml.table import CT_Tbl
from docx.table import _Cell, Table
from docx.text.paragraph import Paragraph
from langchain_text_splitters import RecursiveCharacterTextSplitter

# --- PHẦN 1: UTILITIES TRÍCH XUẤT CẤU TRÚC WORD ---
def iter_block_items(parent):
    """Duyệt Paragraph và Table theo đúng thứ tự trong file Word."""
    if isinstance(parent, Document):
        parent_elm = parent.element.body
    elif isinstance(parent, _Cell):
        parent_elm = parent._tc
    else:
        raise TypeError("Chỉ hỗ trợ Document hoặc Cell")

    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)

def extract_docx(file_path):
    """Trích xuất Word thông minh: Bắt Table, Heading và Auto-numbering."""
    try:
        doc = docx.Document(file_path)
    except KeyError:
        print(f"❌ Lỗi: File {file_path} không đúng định dạng .docx chuẩn.")
        return []
    except Exception as e:
        print(f"❌ Lỗi không xác định: {e}")
        return []
    
    
    final_chunks = [] # Chứa tuple (type, content)
    
    chapter_counter = 0
    section_counter = 0
    
    for block in iter_block_items(doc):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if not text or re.search(r'(Chương|Điều)\s+.*?\d+$', text): continue # Skip Mục lục
            
            style = block.style.name.lower()
            # Check Chương/Điều
            is_chapter = 'heading 1' in style or re.match(r'^\s*(CHƯƠNG|Chương)\s+([IVX\d]+)', text, re.I)
            is_section = 'heading 2' in style or re.match(r'^\s*Điều\s+\d+', text, re.I)
            
            if is_chapter:
                if not re.match(r'^Chương\s+([IVX\d]+)', text, re.I):
                    chapter_counter += 1
                    text = f"Chương {chapter_counter}. {text}"
                final_chunks.append(("chapter", text))
            elif is_section:
                if not re.match(r'^Điều\s+\d+', text, re.I):
                    section_counter += 1
                    text = f"Điều {section_counter}. {text}"
                final_chunks.append(("section", text))
            else:
                # Lọc rác hành chính
                if not re.match(r"^(Nơi nhận|Lưu:|KT\.|HIỆU TRƯỞNG|\(Đã ký\))", text, re.I):
                    final_chunks.append(("text", text))

        elif isinstance(block, Table):
            table_data = []
            is_header_table = False
            for row in block.rows:
                row_cells = [cell.text.strip() for cell in row.cells]
                # Lọc bảng tiêu đề
                if any(kw in " ".join(row_cells) for kw in ["ĐẠI HỌC QUỐC GIA", "CỘNG HÒA XÃ HỘI", "Độc lập - Tự do"]):
                    is_header_table = True; break
                
                clean_row = []
                for i, val in enumerate(row_cells):
                    if i == 0 or val != row_cells[i-1]: clean_row.append(val)
                table_data.append(clean_row)
            
            if table_data and not is_header_table:
                final_chunks.append(("table", table_data))
                
    return final_chunks

def extract_pdf(pdf_path):
    """Trích xuất PDF và chuyển đổi sang cấu trúc blocks (text/table) đồng bộ với Word."""
    blocks = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # 1. Trích xuất bảng
            pdf_tables = page.extract_tables()
            # 2. Trích xuất text
            page_text = page.extract_text()
            
            if page_text:
                for line in page_text.split('\n'):
                    blocks.append(("text", line.strip()))
            
            # Đưa các bảng vào danh sách blocks
            for tbl in pdf_tables:
                if tbl: blocks.append(("table", tbl))
                
    return blocks


# --- PHẦN 2: DỌN DẸP & CHUẨN HÓA VĂN BẢN ---
def clean_administrative(text):
    # --- 1. Xử lý tiêu đề Trường & Tiêu ngữ ---
    titles_to_remove = [
        r"TRƯỜNG\s+ĐẠI\s+HỌC\s+CÔNG\s+NGHỆ\s+THÔNG\s+TIN",
        r"ĐẠI\s+HỌC\s+QUỐC\s+GIA\s+THÀNH\s+PHỐ\s+HỒ\s+CHÍ\s+MINH",
        r"ĐẠI\s+HỌC\s+QUỐC\s+GIA\s+TP\.?\s*HCM",
        r"CỘNG\s+HÒA\s+XÃ\s+HỘI\s+CHỦ\s+NGHĨA\s+VIỆT\s+NAM",
        r"Độc\s+lập\s+[-–]\s+Tự\s+do\s+[-–]\s+Hạnh\s+phúc",
        r"CÔNG\s+N\s*G\s*H\s*Ệ\s+THÔNG\s+TIN",
        r"ĐẠI\s+HỌC\s+QUỐC\s+GIA\s+TP\.\s+HCM",
        r"Độc\s+lập\s+[-–—]\s+Tự\s+do\s+[-–—]\s+Hạnh\s+phúc",
        r"HIỆU\s+TRƯỞNG\s+TRƯỜNG\s+ĐẠI\s+HỌC\s+CÔNG\s+NGHỆ\s+THÔNG\s+TIN",
        r"Thành\s+phố\s+Hồ\s+Chí\s+Minh,\s+ngày\s+\d+\s+tháng\s+\d+\s+năm\s+\d+",
        r"Số:\s+\d+/QĐ-ĐHCNTT",
        r"TRƯỜNG\s+ĐẠI\s+HỌC"
    ]
    for p in titles_to_remove: 
        text = re.sub(p, "", text, flags=re.IGNORECASE | re.MULTILINE)

    # --- 2. Xóa Số hiệu & Ngày tháng ---
    text = re.sub(r"(?m)^\s*Số:\s+\d+/.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?m)^.*ngày\s+\d+\s+tháng\s+\d+\s+năm\s+\d+.*$", "", text, flags=re.IGNORECASE)

    # --- 3. Xóa chân trang & Nơi nhận ---
    footer_patterns = [
        r"(?m)^\s*Nơi\s+nhận:.*$",
        r"(?m)^\s*-\s*Lưu:.*$",
        r"(?m)^\s*HIỆU\s+TRƯỞNG.*$",
        r"(?m)^\s*PHÓ\s+HIỆU\s+TRƯỞNG.*$",
        r"(?m)^\s*KT\.\s+HIỆU\s+TRƯỞNG.*$",
        r"(?m)^\s*\(Đã\s+ký(?:.*)?\)\s*$",
        r"(?m)^\s*-\s*Như\s+Điều\s+\d+.*$",
        r"(?m)^\s*-\s*Lưu:.*$",
        r"(?m)^\s*Quyết\s+định\s+có\s+hiệu\s+lực\s+kể\s+từ\s+ngày\s+ký.*$"
    ]
    for p in footer_patterns: 
        text = re.sub(p, "", text, flags=re.IGNORECASE)

    # --- 4. Sửa lỗi mất chữ ---
    text = re.sub(r"(?m)^inh\s+viên", "Sinh viên", text)
    text = re.sub(r"(?m)^iều\s+(\d+)", r"Điều \1", text)
    text = re.sub(r"(?m)^hương\s+(\d+)", r"Chương \1", text)

    # --- 5. Xóa nhiều dòng trống & Khoảng trắng thừa ---
    text = re.sub(r"\n\s*\n+", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    return text.strip()


# --- PHẦN 3: LOGIC CHUNKING & METADATA ---
def extract_metadata(text, filename):
    metadata = {
        "source": filename,
        "title": None,
        "date": None
    }

    # Title: Tìm "QUY ĐỊNH/TRÌNH" và lấy dòng tiếp theo
    title_match = re.search(r"QUY (ĐỊNH|TRÌNH).*?\n(.*?)\n", text, re.DOTALL)
    if title_match:
        metadata["title"] = title_match.group(2).strip()
    else:
        metadata["title"] = filename

    # Date: Tìm chuỗi ngày tháng năm
    date_match = re.search(r"ngày\s+\d+\s+tháng\s+\d+\s+năm\s+\d+", text, re.I)
    if date_match:
        metadata["date"] = date_match.group(0)

    return metadata

def process_blocks_to_chunks(blocks, metadata_base):
    """Biến danh sách blocks (văn bản + bảng) thành chunks có Metadata xịn."""
    final_chunks = []
    current_chapter = "Quy định chung"
    current_section = "Thông tin chung"
    current_content = []
    current_tables = []

    def save_chunk():
        if current_content or current_tables:
            final_chunks.append({
                "content": "\n".join(current_content).strip(),
                "tables": current_tables.copy(),
                "metadata": {
                    **metadata_base, 
                    "chapter": current_chapter, 
                    "section": current_section
                }
            })
            current_content.clear()
            current_tables.clear()

    for b_type, b_val in blocks:
        if b_type == "chapter":
            save_chunk()
            current_chapter = b_val
        elif b_type == "section":
            save_chunk()
            current_section = b_val
        elif b_type == "table":
            current_tables.append(b_val)
        else:
            current_content.append(b_val)

    save_chunk()
    return final_chunks

# --- PHẦN 4: PIPELINE CHÍNH ---

def process_pipeline(folder_path):
    results = {}
    for filename in os.listdir(folder_path):
        path = os.path.join(folder_path, filename)

         # NHẬN DIỆN LOẠI FILE
        if filename.endswith(".docx"):
            print(f"Processing Word: {filename}")
            blocks = extract_docx(path)
        elif filename.endswith(".pdf"):
            print(f"Processing PDF: {filename}")
            blocks = extract_pdf(path)
        else:
            continue
        
        print(f"Done: {filename}")
        
        # 2. Tạo Metadata cơ sở 
        header_blocks = [b[1] for b in blocks if b[0] == "text"]
        raw_text_for_meta = "\n".join(header_blocks[:15]) 
        # 3. Gọi hàm trích xuất Metadata
        base_meta = extract_metadata(raw_text_for_meta, filename)
        # 3. Gom nhóm thành Chunks theo Chương/Điều
        chunks = process_blocks_to_chunks(blocks, base_meta)
        
        # 4. Recursive Splitting (Nếu chunk nào quá dài)
        refined_chunks = []
        sub_splitter = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=100)
        
        for c in chunks:
            chapter = c['metadata'].get('chapter', 'Quy định chung')
            section = c['metadata'].get('section', 'Thông tin chung')
            prefix = f"[{chapter} - {section}] Nội dung: "
            
            if len(c['content']) > 600:
                splits = sub_splitter.split_text(c['content'])
                for i, s in enumerate(splits):
                    enriched_content = f"{prefix}{s}"
                    refined_chunks.append({
                        "content": enriched_content, 
                        "tables": c['tables'] if i == 0 else [], 
                        "metadata": c['metadata']
                    })
            else:
                enriched_content = f"{prefix}{c['content']}"
                refined_chunks.append({
                    "content": enriched_content, 
                    "tables": c['tables'], 
                    "metadata": c['metadata']
                })
        results[filename] = {"chunks": refined_chunks}
    return results

if __name__ == "__main__":
    DATA_DIR = "data_raw"
    final_data = process_pipeline(DATA_DIR)
    with open("uit_dataset_v2.json", "w", encoding="utf-8") as f:
        json.dump(final_data, f, ensure_ascii=False, indent=4)